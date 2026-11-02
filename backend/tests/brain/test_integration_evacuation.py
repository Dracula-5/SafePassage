"""Evacuations on real PostGIS and the real Andhra Pradesh road graph. The zones, facilities and trucks are test
rows, at real places around Vijayawada so Valhalla can route between them."""

import os
from uuid import uuid4

import asyncpg
import httpx
import pytest

from app.core.db import _init_connection
from app.core.migrate import apply_all
from app.core.settings_brain import BrainSettings
from app.dispatch.messaging import Messenger
from app.graph import evacuation
from app.graph.nodes import Deps
from app.routing.valhalla import ValhallaClient

pytestmark = pytest.mark.integration
ADMIN_URL = os.environ.get("TEST_DATABASE_URL")
VALHALLA_URL = os.environ.get("VALHALLA_URL")
ZONE_SQL = """INSERT INTO hazards (source, source_ref, dedupe_key, hazard_type, severity, headline, geom, fetched_at, expires_at)
              VALUES ($1, $2, $2, $3, $4, $5, ST_Multi(ST_MakeEnvelope($6, $7, $8, $9, 4326)), now(), now() + interval '2 hours')
              RETURNING id"""
FACILITIES = [("assembly", "school", 80.700, 16.540), ("near", "school", 80.800, 16.535), ("far", "shelter", 80.950, 16.450),
              ("west", "hospital", 80.430, 16.310)]  # fmt: skip


@pytest.fixture
async def world(tmp_path):
    if not (ADMIN_URL and VALHALLA_URL):
        pytest.skip("TEST_DATABASE_URL and VALHALLA_URL required")
    admin = await asyncpg.connect(ADMIN_URL)
    name = f"rr_evac_{uuid4().hex[:8]}"
    await admin.execute(f'CREATE DATABASE "{name}"')
    url = f"{ADMIN_URL.rpartition('/')[0]}/{name}"
    pool = await asyncpg.create_pool(url, init=_init_connection)
    async with pool.acquire() as conn:
        await apply_all(conn)
        hazard = await conn.fetchval(ZONE_SQL, "drill", "drill:evac", "flood", "severe", "DRILL: Krishna", 80.60, 16.49, 80.66, 16.53)
        for key, kind, lon, lat in FACILITIES:
            await conn.execute(
                """INSERT INTO facilities (source, source_ref, kind, name, geom, fetched_at)
                   VALUES ('osm', $1, $2, $1, ST_SetSRID(ST_MakePoint($3, $4), 4326), now())""", key, kind, lon, lat)  # fmt: skip
        await conn.execute(
            """INSERT INTO vehicles (label, driver_language, capacity_kg, last_position, last_seen_at)
               VALUES ('TEST-SIM-GPS T1', 'en', 8000, ST_SetSRID(ST_MakePoint(80.75, 16.52), 4326), now())""")
        request = await conn.fetchval(
            "INSERT INTO evacuations (hazard_id, settlement, people, requested_by) VALUES ($1, 'Test Nagar', 40, 'test') RETURNING id", hazard)
    settings = BrainSettings(database_url=url, redis_url="redis://unused", valhalla_url=VALHALLA_URL, documents_dir=tmp_path)
    async with httpx.AsyncClient() as http:
        yield {"deps": Deps(pool=pool, valhalla=ValhallaClient(VALHALLA_URL, http), llm=None, messenger=Messenger(settings, http), settings=settings),
               "pool": pool, "hazard": hazard, "evacuation": request}
    await pool.close()
    await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
    await admin.close()


async def assign(world):
    row = await world["pool"].fetchrow("SELECT * FROM evacuations WHERE id = $1", world["evacuation"])
    await evacuation.assign(world["deps"], row)
    return await world["pool"].fetchrow("SELECT * FROM evacuations WHERE id = $1", world["evacuation"])


async def way_out_enters(pool, route_id) -> list[str]:
    """Headlines of active zones the route after its pickup enters, by PostGIS: a second opinion on Shapely."""
    rows = await pool.fetch(
        """WITH r AS (SELECT geom, stops FROM routes WHERE id = $1),
                cut AS (SELECT ST_LineSubstring(r.geom,
                            ST_LineLocatePoint(r.geom, ST_SetSRID(ST_MakePoint((r.stops->0->>'lon')::float8, (r.stops->0->>'lat')::float8), 4326)),
                            1) AS g FROM r)
           SELECT h.headline FROM v_active_hazards h, cut WHERE ST_Intersects(h.geom, cut.g)""", route_id)  # fmt: skip
    return [r["headline"] for r in rows]


async def test_the_nearest_truck_collects_people_at_the_edge_and_takes_them_to_safety(world):
    pool = world["pool"]
    e = await assign(world)
    assert e["status"] == "assigned", e["failure"]
    assert e["pickup_name"] == "assembly" and e["safe_name"] in ("near", "far") and e["zones_crossed"] == 0
    route = await pool.fetchrow("SELECT * FROM routes WHERE id = $1", e["route_id"])
    assert route["kind"] == "evacuation" and route["status"] == "planned" and route["load_kg"] == 0
    assert [s["kind"] for s in route["stops"]] == ["pickup", "dropoff"]
    assert route["instructions"]["text"].startswith("DRILL - NOT A REAL DISPATCH. EVACUATION.")
    assert "Pick up 40 people from Test Nagar at assembly" in route["instructions"]["text"]
    plan = await pool.fetchrow("SELECT status::text, requires_approval, is_drill, summary, trigger_ref FROM plans WHERE id = $1", e["plan_id"])
    assert plan["status"] == "dispatched" and not plan["requires_approval"] and plan["is_drill"]
    assert plan["trigger_ref"] == f"evacuation:{world['evacuation']}" and "40 people from Test Nagar" in plan["summary"]
    assert await pool.fetchval("SELECT count(*) FROM v_vehicle_assignment") == 1  # so no plan can take this truck too
    assert await way_out_enters(pool, e["route_id"]) == []
    assert await pool.fetchval("SELECT count(*) FROM agent_events WHERE plan_id = $1 AND event_type = 'dispatch'", e["plan_id"]) == 1


async def test_the_way_out_goes_round_a_zone_that_does_not_close_roads(world):
    """A rain forecast is no closure, so goods may drive through it. People on board may not."""
    pool = world["pool"]
    first = await assign(world)
    direct = await pool.fetchrow("SELECT stops, distance_m FROM routes WHERE id = $1", first["route_id"])
    await pool.execute("UPDATE routes SET status = 'abandoned' WHERE id = $1", first["route_id"])
    await pool.execute("UPDATE evacuations SET status = 'waiting', route_id = NULL WHERE id = $1", world["evacuation"])
    await pool.execute(ZONE_SQL, "open_meteo", "cell:1", "heavy_rain", "moderate", "Forecast rain", 80.73, 16.525, 80.76, 16.555)
    e = await assign(world)
    assert e["status"] == "assigned", e["failure"]
    assert e["zones_avoided"] >= 2 and e["zones_crossed"] == 0
    assert await way_out_enters(pool, e["route_id"]) == []
    assert direct["stops"][0]["name"] == "assembly"


async def test_a_pickup_is_never_inside_a_drill_and_a_truck_inside_one_is_not_sent(world):
    pool = world["pool"]
    await pool.execute("UPDATE vehicles SET last_position = ST_SetSRID(ST_MakePoint(80.63, 16.51), 4326)")
    e = await assign(world)
    if e["status"] == "assigned":  # the router may still find it a way out of a drill: the pickup must be clear of it either way
        assert e["pickup_name"] != "Test Nagar"
        assert await way_out_enters(pool, e["route_id"]) == []


async def test_a_truck_cut_off_by_the_zone_is_passed_over(world):
    """Nearest in a straight line, but the only road to the pickup runs through a new drill."""
    pool = world["pool"]
    await pool.execute("UPDATE vehicles SET last_position = ST_SetSRID(ST_MakePoint(80.56, 16.47), 4326), label = 'TEST-SIM-GPS CUT OFF'")
    await pool.execute(ZONE_SQL, "drill", "drill:wall", "flood", "severe", "DRILL: Wall", 80.48, 16.30, 80.60, 16.80)
    await pool.execute(
        """INSERT INTO vehicles (label, driver_language, capacity_kg, last_position, last_seen_at)
           VALUES ('TEST-SIM-GPS REACHES IT', 'en', 8000, ST_SetSRID(ST_MakePoint(80.95, 16.45), 4326), now())""")
    e = await assign(world)
    assert e["status"] == "assigned", e["failure"]
    assert await pool.fetchval("SELECT v.label FROM routes r JOIN vehicles v ON v.id = r.vehicle_id WHERE r.id = $1", e["route_id"]) == "TEST-SIM-GPS REACHES IT"


async def test_it_waits_when_every_truck_is_busy(world):
    await world["pool"].execute("UPDATE vehicles SET last_seen_at = now() - interval '1 hour'")
    e = await assign(world)
    assert e["status"] == "waiting" and e["route_id"] is None


async def test_a_zone_that_has_ended_needs_no_truck(world):
    await world["pool"].execute("UPDATE hazards SET status = 'cancelled'")
    e = await assign(world)
    assert e["status"] == "failed" and "ended" in e["failure"]


async def test_no_way_out_is_said_plainly(world):
    await world["pool"].execute("DELETE FROM facilities")
    e = await assign(world)
    assert e["status"] == "failed" and "no truck can reach this zone" in e["failure"]


async def deviation(pool, e, kind):
    return await pool.fetchval(
        """INSERT INTO route_deviations (route_id, plan_id, vehicle_id, kind)
           SELECT id, plan_id, vehicle_id, $2 FROM routes WHERE id = $1 RETURNING id""", e["route_id"], kind)  # fmt: skip


async def test_a_blocked_truck_with_nobody_on_board_is_replaced(world):
    pool = world["pool"]
    e = await assign(world)
    opened = await deviation(pool, e, "hazard_on_route")
    assert await evacuation.reroute(world["deps"], "hazard_on_route", e["route_id"], opened) is True
    assert await pool.fetchval("SELECT status::text FROM routes WHERE id = $1", e["route_id"]) == "abandoned"
    assert await pool.fetchval("SELECT resolved_at IS NOT NULL FROM route_deviations WHERE id = $1", opened)
    again = await pool.fetchrow("SELECT status, route_id FROM evacuations WHERE id = $1", world["evacuation"])
    assert again["status"] == "waiting" and again["route_id"] is None
    assert (await assign(world))["status"] == "assigned"


async def test_people_on_board_get_a_new_way_out_from_where_the_truck_is(world):
    pool = world["pool"]
    e = await assign(world)
    await pool.execute("UPDATE routes SET completed_stops = 1, status = 'active' WHERE id = $1", e["route_id"])
    await pool.execute("UPDATE vehicles SET last_position = ST_SetSRID(ST_MakePoint(80.72, 16.54), 4326)")
    await pool.execute(ZONE_SQL, "drill", "drill:new", "flood", "severe", "DRILL: New", 80.74, 16.50, 80.78, 16.58)
    opened = await deviation(pool, e, "hazard_on_route")
    assert await evacuation.reroute(world["deps"], "hazard_on_route", e["route_id"], opened) is True
    after = await pool.fetchrow("SELECT * FROM evacuations WHERE id = $1", world["evacuation"])
    assert after["status"] == "assigned", after["failure"]
    assert after["route_id"] != e["route_id"] and after["zones_crossed"] == 0
    new = await pool.fetchrow("SELECT stops, kind FROM routes WHERE id = $1", after["route_id"])
    assert [s["kind"] for s in new["stops"]] == ["dropoff"] and new["kind"] == "evacuation"


async def test_a_crash_while_rerouting_leaves_the_truck_on_its_route(world, monkeypatch):
    pool = world["pool"]
    e = await assign(world)
    await pool.execute("UPDATE routes SET completed_stops = 1, status = 'active' WHERE id = $1", e["route_id"])
    opened = await deviation(pool, e, "hazard_on_route")

    async def router_died(*args, **kwargs):
        raise RuntimeError("the router went away")

    monkeypatch.setattr(evacuation, "way_out", router_died)
    with pytest.raises(RuntimeError):
        await evacuation.reroute(world["deps"], "hazard_on_route", e["route_id"], opened)
    assert await pool.fetchval("SELECT status::text FROM routes WHERE id = $1", e["route_id"]) == "active"
    assert await pool.fetchval("SELECT resolved_at IS NULL FROM route_deviations WHERE id = $1", opened)  # the sweep tries again


async def test_a_safe_point_taken_by_a_new_drill_is_not_used(world):
    pool = world["pool"]
    e = await assign(world)
    safe = await pool.fetchval("SELECT stops->-1->>'name' FROM routes WHERE id = $1", e["route_id"])
    await pool.execute("UPDATE routes SET completed_stops = 1, status = 'active' WHERE id = $1", e["route_id"])
    await pool.execute("UPDATE vehicles SET last_position = ST_SetSRID(ST_MakePoint(80.72, 16.54), 4326)")
    lon, lat = next((lo, la) for key, _, lo, la in FACILITIES if key == safe)
    await pool.execute(ZONE_SQL, "drill", "drill:safe", "flood", "severe", "DRILL: Over the safe point", lon - 0.02, lat - 0.02, lon + 0.02, lat + 0.02)
    opened = await deviation(pool, e, "hazard_on_route")
    assert await evacuation.reroute(world["deps"], "hazard_on_route", e["route_id"], opened) is True
    after = await pool.fetchrow("SELECT status, safe_name, failure FROM evacuations WHERE id = $1", world["evacuation"])
    assert after["status"] == "assigned", after["failure"]
    assert after["safe_name"] != safe


async def test_a_lost_truck_with_people_on_board_goes_to_a_person(world):
    pool = world["pool"]
    e = await assign(world)
    await pool.execute("UPDATE routes SET completed_stops = 1, status = 'active' WHERE id = $1", e["route_id"])
    opened = await deviation(pool, e, "offline")
    assert await evacuation.reroute(world["deps"], "offline", e["route_id"], opened) is True
    after = await pool.fetchrow("SELECT status, failure FROM evacuations WHERE id = $1", world["evacuation"])
    assert after["status"] == "failed" and "40 people on board" in after["failure"]


async def test_a_slow_router_never_touches_the_breaker_planning_shares(world):
    """Timeouts here must not fail every plan as "routing service unavailable"."""
    deps = world["deps"]
    router = evacuation._router(deps)
    assert router is not deps.valhalla and router.breaker is not deps.valhalla.breaker
    for _ in range(3):
        router.breaker.record_failure()
    assert deps.valhalla.breaker.state == "closed"


async def test_a_timed_out_search_counts_as_no_way_found(world, monkeypatch):
    deps = world["deps"]
    router = evacuation._router(deps)

    async def too_slow(*args, **kwargs):
        raise evacuation.SourceUnavailable("valhalla_evacuation", "ReadTimeout")

    monkeypatch.setattr(router, "route", too_slow)
    safe = evacuation.Spot("x", "x", 80.8, 16.535)
    assert await evacuation.way_out(deps, (80.70, 16.54), safe, True) is None


async def test_a_router_that_is_down_leaves_people_waiting_not_told_there_is_no_road(world, monkeypatch):
    deps, pool = world["deps"], world["pool"]

    async def no_way(*args, **kwargs):
        return None

    async def down():
        raise evacuation.SourceUnavailable("valhalla", "ConnectError")

    monkeypatch.setattr(evacuation, "way_out", no_way)
    monkeypatch.setattr(deps.valhalla, "status", down)
    row = await pool.fetchrow("SELECT * FROM evacuations WHERE id = $1", world["evacuation"])
    with pytest.raises(evacuation.SourceUnavailable):
        await evacuation.assign(deps, row)
    assert await pool.fetchval("SELECT status FROM evacuations WHERE id = $1", world["evacuation"]) == "waiting"


async def test_a_goods_route_is_left_to_the_replanner(world):
    assert await evacuation.reroute(world["deps"], "offline", uuid4(), uuid4()) is False
