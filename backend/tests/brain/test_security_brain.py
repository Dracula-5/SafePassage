"""What must not go wrong in the Brain: messages from drills, bad names in PDFs, events that never
clear, two workers at once, a phone that asks for endless replans.

Bus tests need TEST_REDIS_URL, singleton tests TEST_DATABASE_URL, graph tests also VALHALLA_URL.
"""

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pypdf import PdfReader
from reliefroute_contracts.events import PlanRequested, VehicleDeviation, envelope_adapter

from app.agents import reasoning
from app.agents.schemas import OrderDraft, OrderLineDraft
from app.core import bus, db
from app.core.config import get_common_settings
from app.core.db import Singleton
from app.core.migrate import apply_all, grant_app_role
from app.dispatch.documents import purchase_order_pdf
from app.dispatch.messaging import SendResult
from app.graph.monitor import RouteMonitor

from .test_integration_closed_loop import active_route, published, run_to_dispatch  # noqa: F401 (fixture)
from .test_integration_graph import events, world  # noqa: F401 (fixture)

REDIS_URL = os.environ.get("TEST_REDIS_URL")
ADMIN_URL = os.environ.get("TEST_DATABASE_URL")


def order(name: str) -> OrderDraft:
    return OrderDraft(order_key="ORD-1", supplier_id="s", supplier_name=name, supplier_verified=False, supplier_facility_id="f",
                      supplier_lon=80.6, supplier_lat=16.5, destination_facility_id="d",
                      lines=[OrderLineDraft(item_code="RICE_KG", quantity=10)], weight_kg=10, travel_s=None)  # fmt: skip


def text_of(path) -> str:
    return " ".join(page.extract_text() for page in PdfReader(path).pages)


@pytest.mark.parametrize("name", ["A < B Traders", "Krishna & Sons", "</para><b>", '<img src="file:///etc/passwd"/>', "<font size=99>"])
def test_a_name_in_a_pdf_is_printed_never_obeyed(tmp_path, name):
    path = purchase_order_pdf(tmp_path / "po.pdf", "plan", order(name), "<a href='x'>Relief point</a>", "key", False,
                              datetime.now(UTC))  # fmt: skip
    assert name in text_of(path)


def test_prompts_mark_outside_text_as_data():
    for system in (reasoning.SENTINEL_SYSTEM, reasoning.BRIEF_SYSTEM):
        assert reasoning.DATA_NOT_INSTRUCTIONS in system


# ---------------------------------------------------------------- event bus
@pytest.fixture
async def stream_name(monkeypatch):
    if not REDIS_URL:
        pytest.skip("TEST_REDIS_URL not set")
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    monkeypatch.setattr(bus, "RECLAIM_IDLE_MS", 0)
    monkeypatch.setattr(bus, "BLOCK_MS", 20)
    monkeypatch.setattr(bus, "MAX_DELIVERIES", 3)
    get_common_settings.cache_clear()
    await bus.close_redis()
    name = f"test.{uuid4().hex[:8]}"
    yield name
    await bus.get_redis().delete(name, bus.DEAD_LETTER_PREFIX + name)
    await bus.close_redis()
    get_common_settings.cache_clear()


def event_data() -> dict:
    payload = PlanRequested(plan_id=uuid4(), trigger_kind="manual", requested_by="test")
    return {"data": envelope_adapter.dump_json(bus.build_envelope("plan.requested", payload)).decode()}


async def run_until(condition, stream: str, handler, group: str = "g") -> None:
    await bus.ensure_group(stream, group)
    stop = asyncio.Event()

    async def watch():
        while not await condition():
            await asyncio.sleep(0.02)
        stop.set()

    await asyncio.wait_for(asyncio.gather(bus.consume([stream], group, "c", handler, stop), watch()), timeout=10)


async def test_an_event_that_always_fails_is_parked_not_retried_forever(stream_name):
    client = bus.get_redis()
    await bus.ensure_group(stream_name, "g")
    await client.xadd(stream_name, event_data())
    attempts = []

    async def broken(_):
        attempts.append(1)
        raise ValueError("a bug, not an outage")

    async def parked():
        return await client.xlen(bus.DEAD_LETTER_PREFIX + stream_name) == 1

    await run_until(parked, stream_name, broken)
    assert len(attempts) == bus.MAX_DELIVERIES
    assert (await client.xpending(stream_name, "g"))["pending"] == 0
    dead = await client.xrange(bus.DEAD_LETTER_PREFIX + stream_name)
    assert "a bug, not an outage" in dead[0][1]["error"]
    assert await bus.dead_letter_count() >= 1


@pytest.mark.parametrize("outage", [ConnectionError("db gone"), asyncpg.CannotConnectNowError("starting")])
async def test_an_outage_never_costs_an_event_however_long_it_lasts(stream_name, outage):
    client = bus.get_redis()
    await bus.ensure_group(stream_name, "g")
    await client.xadd(stream_name, event_data())
    attempts = []

    async def down(_):
        attempts.append(1)
        if len(attempts) <= bus.MAX_DELIVERIES * 2:
            raise outage

    async def delivered():
        return (await client.xpending(stream_name, "g"))["pending"] == 0 and len(attempts) > bus.MAX_DELIVERIES * 2

    await run_until(delivered, stream_name, down)
    assert await client.xlen(bus.DEAD_LETTER_PREFIX + stream_name) == 0


async def test_consumers_recover_when_redis_loses_their_group(stream_name):
    client = bus.get_redis()
    await bus.ensure_group(stream_name, "g")
    received = []

    async def handler(envelope):
        received.append(envelope)

    async def received_one():
        return bool(received)

    async def wipe_then_publish():
        await asyncio.sleep(0.1)
        await client.xgroup_destroy(stream_name, "g")  # what a Redis restarted without its volume looks like
        await asyncio.sleep(0.3)
        await client.xadd(stream_name, event_data())

    await asyncio.gather(run_until(received_one, stream_name, handler), wipe_then_publish())
    assert len(received) == 1


# ---------------------------------------------------------------- one worker at a time
@pytest.fixture
def lock_db(monkeypatch):
    if not ADMIN_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    monkeypatch.setenv("DATABASE_URL", ADMIN_URL)
    monkeypatch.setattr(db, "STANDBY_POLL_S", 0.02)
    monkeypatch.setattr(db, "LOCK_CHECK_S", 0.02)
    get_common_settings.cache_clear()
    yield f"test-{uuid4().hex[:8]}"
    get_common_settings.cache_clear()


async def test_a_second_worker_waits_and_takes_over_when_the_first_stops(lock_db):
    first, second = Singleton(lock_db), Singleton(lock_db)
    await first.acquire()
    standby = asyncio.create_task(second.acquire())
    await asyncio.sleep(0.15)
    assert not standby.done()

    await first.release()
    await asyncio.wait_for(standby, timeout=5)
    await second.release()


async def test_a_worker_takes_its_lock_back_after_postgres_drops_it(lock_db):
    worker = Singleton(lock_db)
    await worker.acquire()
    keeper = asyncio.create_task(worker.keep())
    dropped = worker.conn
    admin = await asyncpg.connect(ADMIN_URL)
    await admin.execute("SELECT pg_terminate_backend($1)", dropped.get_server_pid())

    async def relocked():
        while worker.conn is dropped:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(relocked(), timeout=5)
    other = Singleton(lock_db)
    assert await other._try() is False
    keeper.cancel()
    await worker.release()
    await admin.close()


async def test_a_worker_stops_if_someone_else_took_over(lock_db):
    worker, rival = Singleton(lock_db), Singleton(lock_db)
    await worker.acquire()
    admin = await asyncpg.connect(ADMIN_URL)
    await admin.execute("SELECT pg_terminate_backend($1)", worker.conn.get_server_pid())
    await rival.acquire()  # the rival wins the race while the first worker is disconnected
    with pytest.raises(RuntimeError, match="took over"):
        await asyncio.wait_for(worker.keep(), timeout=5)
    await rival.release()
    await admin.close()


@pytest.fixture
async def app_role_url():
    if not ADMIN_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    admin = await asyncpg.connect(ADMIN_URL)
    suffix = uuid4().hex[:8]
    database, role = f"rr_role_{suffix}", f"rr_app_{suffix}"
    await admin.execute(f'CREATE DATABASE "{database}"')
    base = ADMIN_URL.rpartition("/")[0]
    conn = await asyncpg.connect(f"{base}/{database}")
    await apply_all(conn)
    await grant_app_role(conn, role, "app-pass'word")
    await conn.close()
    host = base.rpartition("@")[2]
    yield f"postgresql://{role}:app-pass%27word@{host}/{database}"
    await admin.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
    await admin.execute(f'DROP ROLE "{role}"')
    await admin.close()


@pytest.mark.integration
async def test_a_database_first_migrated_on_windows_migrates_cleanly_on_linux(app_role_url):
    admin = await asyncpg.connect(ADMIN_URL.rpartition("/")[0] + "/" + app_role_url.rpartition("/")[2])
    await admin.execute(r"UPDATE schema_migrations SET filename = replace(filename, '/', '\')")
    assert await apply_all(admin) == []  # nothing runs again
    assert await admin.fetchval(r"SELECT count(*) FROM schema_migrations WHERE filename LIKE '%\\%'") == 0
    await admin.close()


@pytest.mark.integration
async def test_the_service_role_can_run_the_system(app_role_url):
    conn = await asyncpg.connect(app_role_url)
    vehicle = await conn.fetchval("INSERT INTO vehicles (label, driver_language, capacity_kg) VALUES ('T', 'en', 100) RETURNING id")
    await conn.execute("INSERT INTO telemetry (vehicle_id, geom, recorded_at) VALUES ($1, ST_SetSRID(ST_MakePoint(80, 16), 4326), now())", vehicle)
    await conn.execute("CREATE TEMP TABLE facility_load (id int)")  # bulk loads of OSM data use these
    await conn.execute("DELETE FROM telemetry")
    await conn.close()
    async with AsyncPostgresSaver.from_conn_string(app_role_url) as saver:
        await saver.setup()  # the checkpointer creates its own tables at start-up


@pytest.mark.integration
@pytest.mark.parametrize("attack", [
    "COPY (SELECT 1) TO PROGRAM 'id'",
    "SELECT pg_read_file('postgresql.conf')",
    "CREATE EXTENSION IF NOT EXISTS plpython3u",
    "CREATE ROLE intruder SUPERUSER",
    "ALTER ROLE CURRENT_USER SUPERUSER",
    "DROP TABLE vehicles",
    "ALTER TABLE vehicles ADD COLUMN stolen text",
])  # fmt: skip
async def test_the_service_role_cannot_reach_past_the_tables(app_role_url, attack):
    conn = await asyncpg.connect(app_role_url)
    with pytest.raises(asyncpg.PostgresError):
        await conn.execute(attack)
    await conn.close()


# ---------------------------------------------------------------- the plan pipeline
@pytest.mark.integration
async def test_a_drill_never_messages_a_real_person(world, published, monkeypatch):  # noqa: F811
    sent = []

    async def send(channel, to, body):
        sent.append(to)
        return SendResult("sent", "SM1", None)

    monkeypatch.setattr(world["deps"].messenger, "send", send)
    await run_to_dispatch(world)
    assert sent == []
    notes = await world["pool"].fetch("SELECT status, error FROM notifications")
    assert notes and all(n["status"] == "failed" and "drills do not message" in n["error"] for n in notes)


@pytest.mark.integration
async def test_an_operator_can_choose_to_send_drill_messages(world, published, monkeypatch):  # noqa: F811
    sent = []

    async def send(channel, to, body):
        sent.append(body)
        return SendResult("sent", "SM1", None)

    monkeypatch.setattr(world["deps"].messenger, "send", send)
    world["deps"].settings.drill_send_messages = True
    await run_to_dispatch(world)
    assert sent and all("DRILL" in body for body in sent)


@pytest.mark.integration
async def test_a_full_disk_does_not_turn_a_dispatched_plan_into_a_failed_one(world, published, tmp_path):  # noqa: F811
    blocked = tmp_path / "not-a-folder"
    blocked.write_text("x")
    world["deps"].settings.documents_dir = blocked
    await run_to_dispatch(world)
    pool, plan = world["pool"], world["plan"]
    assert await pool.fetchval("SELECT status::text FROM plans WHERE id = $1", plan) == "dispatched"
    assert await pool.fetchval("SELECT count(*) FROM routes") >= 1
    assert any("was not written" in e["message"] for e in await events(pool, plan))


async def deviation_on(pool, route, plan, kind: str) -> VehicleDeviation:
    detected = await pool.fetchval(
        "INSERT INTO route_deviations (route_id, plan_id, vehicle_id, kind) VALUES ($1, $2, $3, $4) RETURNING detected_at",
        route["id"], plan, route["vehicle_id"], kind)
    return VehicleDeviation(vehicle_id=route["vehicle_id"], plan_id=plan, route_id=route["id"], kind=kind, detected_at=detected)


async def fill_replan_budget(pool, route, plan, count: int) -> None:
    for _ in range(count):
        await pool.execute(
            """INSERT INTO route_deviations (route_id, plan_id, vehicle_id, kind, resolved_at, replan_id)
               VALUES ($1, $2, $3, 'off_route', now(), $2)""", route["id"], plan, route["vehicle_id"])  # fmt: skip


async def replans(pool) -> int:
    return await pool.fetchval("SELECT count(*) FROM plans WHERE trigger_kind = 'replan'")


@pytest.mark.integration
async def test_a_faulty_phone_cannot_ask_for_endless_replans(world, published):  # noqa: F811
    pool, plan = world["pool"], world["plan"]
    await run_to_dispatch(world)
    route = await active_route(pool)
    monitor = RouteMonitor(world["deps"])
    await fill_replan_budget(pool, route, plan, world["deps"].settings.max_replans_per_hour)

    deviation = await deviation_on(pool, route, plan, "off_route")
    await monitor.on_deviation(deviation)
    await monitor.on_deviation(deviation)  # the sweep retries; the human hears about it once
    assert await replans(pool) == 0
    warnings = [e for e in await events(pool, plan) if "automatic re-routing is paused" in e["message"]]
    assert len(warnings) == 1


@pytest.mark.integration
async def test_a_hazard_ahead_is_never_held_back_by_the_replan_cap(world, published):  # noqa: F811
    pool, plan = world["pool"], world["plan"]
    await run_to_dispatch(world)
    route = await active_route(pool)
    await fill_replan_budget(pool, route, plan, world["deps"].settings.max_replans_per_hour)
    await RouteMonitor(world["deps"]).on_deviation(await deviation_on(pool, route, plan, "hazard_on_route"))
    assert await replans(pool) == 1


@pytest.mark.integration
async def test_replanning_resumes_when_the_hour_has_passed(world, published):  # noqa: F811
    pool, plan = world["pool"], world["plan"]
    await run_to_dispatch(world)
    route = await active_route(pool)
    monitor = RouteMonitor(world["deps"])
    await fill_replan_budget(pool, route, plan, world["deps"].settings.max_replans_per_hour)
    deviation = await deviation_on(pool, route, plan, "off_route")
    await monitor.on_deviation(deviation)
    assert await replans(pool) == 0

    await pool.execute("UPDATE route_deviations SET detected_at = detected_at - interval '2 hours' WHERE replan_id IS NOT NULL")
    await monitor.on_deviation(deviation)
    assert await replans(pool) == 1
