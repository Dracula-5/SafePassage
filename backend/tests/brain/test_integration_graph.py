"""Full agent graph on real PostGIS + real Valhalla (Andhra Pradesh graph) + Postgres checkpointer.

The LLM is a scripted test double (first Sentinel reply is malformed on purpose). All rows below
are test fixtures written into a throwaway database; coordinates are real places around
Vijayawada so Valhalla can route between them.
"""

import os
from uuid import uuid4

import asyncpg
import httpx
import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.core.db import _init_connection
from app.core.migrate import apply_all
from app.core.settings_brain import BrainSettings
from app.dispatch.messaging import Messenger
from app.graph.build import PlanRunner
from app.graph.dispatcher import dispatch
from app.graph.audit import Audit
from app.graph.nodes import Deps
from app.llm.client import LLMClient
from app.routing.valhalla import ValhallaClient

from .fakes import SchemaAwareBackend

pytestmark = pytest.mark.integration
ADMIN_URL = os.environ.get("TEST_DATABASE_URL")
VALHALLA_URL = os.environ.get("VALHALLA_URL")


@pytest.fixture
async def world(tmp_path):
    if not (ADMIN_URL and VALHALLA_URL):
        pytest.skip("TEST_DATABASE_URL and VALHALLA_URL required")
    admin = await asyncpg.connect(ADMIN_URL)
    name = f"rr_graph_{uuid4().hex[:8]}"
    await admin.execute(f'CREATE DATABASE "{name}"')
    url = f"{ADMIN_URL.rpartition('/')[0]}/{name}"
    pool = await asyncpg.create_pool(url, init=_init_connection)
    async with pool.acquire() as conn:
        await apply_all(conn)
        hazard = await conn.fetchval(
            """INSERT INTO hazards (source, source_ref, dedupe_key, hazard_type, severity, headline, geom, fetched_at, expires_at)
               VALUES ('drill', 'drill:test', 'drill:test', 'flood', 'severe', 'DRILL: Krishna flood',
                       ST_Multi(ST_MakeEnvelope(80.60, 16.49, 80.66, 16.53, 4326)), now(), now() + interval '2 hours')
               RETURNING id""")
        await conn.execute(
            """INSERT INTO places (source, source_ref, place_type, name, geom, population, population_source, fetched_at)
               VALUES ('osm', 'node/1', 'suburb', 'A', ST_SetSRID(ST_MakePoint(80.62, 16.51), 4326), 600, 'census2011', now()),
                      ('osm', 'node/2', 'suburb', 'B', ST_SetSRID(ST_MakePoint(80.64, 16.52), 4326), 400, 'census2011', now()),
                      ('osm', 'node/3', 'hamlet', 'C', ST_SetSRID(ST_MakePoint(80.63, 16.50), 4326), NULL, NULL, now())""")
        fac = {}
        for key, kind, lon, lat in [("school_e", "school", 80.700, 16.540), ("school_w", "school", 80.560, 16.470),
                                    ("depot", "warehouse", 80.800, 16.535)]:
            fac[key] = await conn.fetchval(
                """INSERT INTO facilities (source, source_ref, kind, name, geom, fetched_at)
                   VALUES ('osm', $1, $2, $3, ST_SetSRID(ST_MakePoint($4, $5), 4326), now()) RETURNING id""",
                f"node/{key}", kind, key, lon, lat)
        supplier = await conn.fetchval(
            "INSERT INTO suppliers (facility_id, name, contact_whatsapp) VALUES ($1, 'Test Depot', '+919000000009') RETURNING id",
            fac["depot"])
        await conn.execute(
            """INSERT INTO stock (supplier_id, item_code, quantity, unit_weight_kg, reported_at, reported_via) VALUES
               ($1, 'WATER_L', 20000, 1, now(), 'portal'), ($1, 'RICE_KG', 300, 1, now(), 'portal')""", supplier)
        await conn.execute(
            """INSERT INTO vehicles (label, driver_phone, driver_language, capacity_kg, last_position, last_seen_at)
               VALUES ('AP-T1', '+919000000010', 'te', 6000, ST_SetSRID(ST_MakePoint(80.75, 16.52), 4326), now())""")
        plan = await conn.fetchval(
            "INSERT INTO plans (thread_id, trigger_kind, hazard_ids, requested_by) VALUES ($1, 'drill', $2, 'test') RETURNING id",
            f"thread-{uuid4()}", [hazard])
    settings = BrainSettings(database_url=url, redis_url="redis://unused", valhalla_url=VALHALLA_URL, documents_dir=tmp_path)
    async with httpx.AsyncClient() as http, AsyncPostgresSaver.from_conn_string(url) as saver:
        await saver.setup()
        backend = SchemaAwareBackend(malformed_first=True)
        deps = Deps(pool=pool, valhalla=ValhallaClient(VALHALLA_URL, http), llm=LLMClient(backend),
                    messenger=Messenger(settings, http), settings=settings)
        yield {"runner": PlanRunner(deps, saver), "deps": deps, "pool": pool, "plan": str(plan), "hazard": str(hazard),
               "backend": backend, "docs": tmp_path}
    await pool.close()
    await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
    await admin.close()


async def events(pool, plan):
    return await pool.fetch("SELECT node::text, event_type::text, message, source_ids FROM agent_events WHERE plan_id = $1 ORDER BY id", plan)


async def test_graph_pauses_for_approval_then_dispatches_idempotently(world):
    runner, pool, plan = world["runner"], world["pool"], world["plan"]
    thread = await pool.fetchval("SELECT thread_id FROM plans WHERE id = $1", plan)
    await runner.start(plan, thread, [world["hazard"]])

    assert await pool.fetchval("SELECT status::text FROM plans WHERE id = $1", plan) == "awaiting_approval"
    timeline = await events(pool, plan)
    nodes = [e["node"] for e in timeline]
    for node in ("sentinel", "needs", "procurement", "route_planner", "critic", "supervisor"):
        assert node in nodes, node
    sentinel_llm = next(e for e in timeline if e["node"] == "sentinel" and e["event_type"] == "tool_call")
    assert "LLM triage" in sentinel_llm["message"]
    assert world["backend"].calls[:2] == ["sentinel-malformed", "sentinel"]  # repaired on the second try
    assert any(f"hazard:{world['hazard']}" in e["source_ids"] for e in timeline)
    needs = next(e for e in timeline if e["node"] == "needs")
    assert "census-tagged settlements only (67% settlement coverage)" in needs["message"]
    procurement = next(e for e in timeline if e["node"] == "procurement" and e["event_type"] == "decision")
    assert "1 shortfall" in procurement["message"]  # 300 kg rice held vs 575 kg needed
    assert await pool.fetchval("SELECT count(*) FROM orders") == 0  # nothing dispatched before approval

    await pool.execute("INSERT INTO approvals (plan_id, decision, decided_by) VALUES ($1, 'approved', 'duty officer')", plan)
    await runner.resume(thread, {"decision": "approved", "decided_by": "duty officer", "reason": None})

    assert await pool.fetchval("SELECT status::text FROM plans WHERE id = $1", plan) == "dispatched"
    orders = await pool.fetch("SELECT idempotency_key, status::text FROM orders")
    assert orders and all(o["status"] == "dispatched" for o in orders)
    route = await pool.fetchrow("SELECT instructions, min_hazard_clearance_m, ST_NPoints(geom) AS n FROM routes")
    assert route["instructions"]["language"] == "te" and route["instructions"]["text"].startswith("[te] DRILL")
    assert route["min_hazard_clearance_m"] >= 100 and route["n"] > 10
    notes = await pool.fetch("SELECT status, error FROM notifications")
    assert notes and all(n["status"] == "failed" and "drills do not message" in n["error"] for n in notes)
    assert list(world["docs"].glob("*/PO-*.pdf"))
    assert await pool.fetchval("SELECT route_status::text FROM v_vehicle_assignment") == "planned"

    # Idempotency: resuming again does nothing; re-running the dispatcher creates nothing new.
    counts = [await pool.fetchval(f"SELECT count(*) FROM {t}") for t in ("orders", "order_lines", "routes", "notifications")]
    assert await runner.resume(thread, {"decision": "approved", "decided_by": "x"}) is None
    state = (await runner.graph.aget_state(runner.config(thread))).values
    await dispatch(world["deps"], Audit(pool, plan, thread), state)
    assert [await pool.fetchval(f"SELECT count(*) FROM {t}") for t in ("orders", "order_lines", "routes", "notifications")] == counts
    assert any("already handled (idempotent)" in e["message"] for e in await events(pool, plan))


async def test_rejection_and_missing_vehicle_paths(world):
    runner, pool, plan = world["runner"], world["pool"], world["plan"]
    await pool.execute("UPDATE vehicles SET last_seen_at = now() - interval '1 hour'")  # driver offline
    thread = await pool.fetchval("SELECT thread_id FROM plans WHERE id = $1", plan)
    await runner.start(plan, thread, [world["hazard"]])
    row = await pool.fetchrow("SELECT status::text, unreachable_reason FROM plans WHERE id = $1", plan)
    assert row["status"] == "failed" and "no online vehicle" in row["unreachable_reason"]
    assert await pool.fetchval("SELECT count(*) FROM orders") == 0
