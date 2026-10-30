"""Stale approvals and the closed loop on real PostGIS + real Valhalla + Postgres checkpointer.

Telemetry events are test fixtures built here; the route, hazards and replans are computed for real.
Published events are captured instead of going through Redis so each step can be asserted.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from reliefroute_contracts.events import HazardUpdated, LonLat, VehicleTelemetry
from reliefroute_contracts.streams import VEHICLE_DEVIATION

from app.agents.data import population_in
from app.core import bus
from app.graph import plans
from app.graph.monitor import RouteMonitor

from .test_integration_graph import events, world  # noqa: F401 (fixture)

pytestmark = pytest.mark.integration
OFF_ROUTE = (80.72, 16.62)  # a road well away from anything planned around Vijayawada


@pytest.fixture
def published(monkeypatch):
    seen = []

    async def capture(event_type, payload, dedupe_key=None):
        seen.append((event_type, payload))

    monkeypatch.setattr(bus, "publish", capture)
    return seen


async def run_to_dispatch(world):  # noqa: F811
    runner, pool, plan = world["runner"], world["pool"], world["plan"]
    thread = await pool.fetchval("SELECT thread_id FROM plans WHERE id = $1", plan)
    await runner.start(plan, thread, [world["hazard"]])
    assert await pool.fetchval("SELECT status::text FROM plans WHERE id = $1", plan) == "awaiting_approval"
    await pool.execute("INSERT INTO approvals (plan_id, decision, decided_by) VALUES ($1, 'approved', 'officer')", plan)
    await runner.resume(thread, {"decision": "approved", "decided_by": "officer", "reason": None})
    return thread


async def fix(pool, vehicle_id, point, accuracy=10.0):
    await pool.execute(
        """UPDATE vehicles SET last_position = ST_SetSRID(ST_MakePoint($2, $3), 4326), last_seen_at = now() WHERE id = $1""",
        vehicle_id, *point)
    return VehicleTelemetry(telemetry_id=1, vehicle_id=vehicle_id, position=LonLat(lon=point[0], lat=point[1]),
                            recorded_at=datetime.now(UTC), accuracy_m=accuracy)  # fmt: skip


async def active_route(pool):
    return await pool.fetchrow("SELECT id, vehicle_id, stops, status::text FROM routes WHERE status IN ('planned', 'active')")


async def replan_from(world, published):  # noqa: F811
    """Feed the published deviation to the monitor, then run the plan it requests, as the worker does."""
    runner, pool = world["runner"], world["pool"]
    monitor = RouteMonitor(world["deps"])
    deviation = next(p for t, p in published if t == VEHICLE_DEVIATION)
    await monitor.on_deviation(deviation)
    child = await pool.fetchval("SELECT id::text FROM plans WHERE parent_plan_id IS NOT NULL ORDER BY created_at DESC LIMIT 1")
    claimed = await plans.claim_for_planning(pool, child)
    await runner.start_replan(claimed["id"], claimed["thread_id"], list(claimed["hazard_ids"]), claimed["parent_plan_id"],
                              claimed["trigger_ref"])  # fmt: skip
    return child


async def test_city_population_is_scaled_by_its_mandal_and_suburbs_are_not_counted_twice(world):  # noqa: F811
    pool, hazard = world["pool"], world["hazard"]
    await pool.execute(
        """INSERT INTO admin_areas (source_ref, admin_level, name, name_norm, geom, fetched_at)
           VALUES ('relation/9', 6, 'Test Mandal', 'test mandal', ST_Multi(ST_MakeEnvelope(80.60, 16.45, 80.66, 16.57, 4326)), now())""")
    await pool.execute(
        """INSERT INTO places (source, source_ref, place_type, name, geom, population, population_source, fetched_at)
           VALUES ('osm', 'node/10', 'city', 'Big City', ST_SetSRID(ST_MakePoint(80.63, 16.51), 4326), 1000000, 'census2011', now())""")
    estimate = await population_in(pool, [hazard])
    # The drill covers a third of the mandal (0.04 of 0.12 degrees of latitude). Suburbs A and B share
    # the city's mandal, so their 1,000 people are inside its figure; hamlet C has no figure.
    assert estimate.population_lower_bound == pytest.approx(1_000_000 / 3, rel=0.01)
    assert estimate.population_unscaled == 1_001_000 and estimate.places_scaled == 1


async def test_town_without_a_mandal_boundary_still_counts_in_full(world):  # noqa: F811
    pool = world["pool"]
    await pool.execute(
        """INSERT INTO places (source, source_ref, place_type, name, geom, population, fetched_at)
           VALUES ('osm', 'node/11', 'town', 'Unmapped', ST_SetSRID(ST_MakePoint(80.63, 16.52), 4326), 5000, now())""")
    assert (await population_in(pool, [world["hazard"]])).population_lower_bound == 6000  # + suburbs A and B


async def test_approval_after_the_hazard_ended_does_not_dispatch(world, published):  # noqa: F811
    runner, pool, plan = world["runner"], world["pool"], world["plan"]
    thread = await pool.fetchval("SELECT thread_id FROM plans WHERE id = $1", plan)
    await runner.start(plan, thread, [world["hazard"]])
    await pool.execute("UPDATE hazards SET status = 'expired' WHERE id = $1", world["hazard"])
    await runner.resume(thread, {"decision": "approved", "decided_by": "officer", "reason": None})

    row = await pool.fetchrow("SELECT status::text, unreachable_reason FROM plans WHERE id = $1", plan)
    assert row["status"] == "superseded" and "no longer active" in row["unreachable_reason"]
    assert await pool.fetchval("SELECT count(*) FROM orders") == 0 and await pool.fetchval("SELECT count(*) FROM routes") == 0
    assert not published  # nothing left to plan for


async def test_stock_taken_while_waiting_supersedes_the_plan_and_requests_a_fresh_one(world, published):  # noqa: F811
    runner, pool, plan = world["runner"], world["pool"], world["plan"]
    thread = await pool.fetchval("SELECT thread_id FROM plans WHERE id = $1", plan)
    await runner.start(plan, thread, [world["hazard"]])
    await pool.execute("UPDATE stock SET quantity = 1, reported_at = now()")
    await runner.resume(thread, {"decision": "approved", "decided_by": "officer", "reason": None})

    row = await pool.fetchrow("SELECT status::text, unreachable_reason FROM plans WHERE id = $1", plan)
    assert row["status"] == "superseded" and "plan needs" in row["unreachable_reason"]
    assert await pool.fetchval("SELECT count(*) FROM orders") == 0
    child = await pool.fetchrow("SELECT trigger_kind::text, trigger_ref, parent_plan_id::text FROM plans WHERE id <> $1", plan)
    assert child["trigger_kind"] == "replan" and child["trigger_ref"] is None and child["parent_plan_id"] == plan


async def test_stops_advance_from_gps_and_orders_follow(world, published):  # noqa: F811
    pool, plan = world["pool"], world["plan"]
    await run_to_dispatch(world)
    route = await active_route(pool)
    monitor = RouteMonitor(world["deps"])
    assert route["status"] == "planned"

    first = route["stops"][0]
    await monitor.on_telemetry(await fix(pool, route["vehicle_id"], (first["lon"], first["lat"])))
    row = await pool.fetchrow("SELECT status::text, completed_stops, started_at FROM routes WHERE id = $1", route["id"])
    assert row["status"] == "active" and row["completed_stops"] >= 1 and row["started_at"]
    assert await pool.fetchval("SELECT count(*) FROM orders WHERE status = 'in_transit'") >= 1

    for stop in route["stops"]:
        await monitor.on_telemetry(await fix(pool, route["vehicle_id"], (stop["lon"], stop["lat"])))
    row = await pool.fetchrow("SELECT status::text, completed_stops, completed_at FROM routes WHERE id = $1", route["id"])
    assert row["status"] == "completed" and row["completed_stops"] == len(route["stops"]) and row["completed_at"]
    assert await pool.fetchval("SELECT count(*) FROM orders WHERE status <> 'delivered'") == 0
    assert any("route complete" in e["message"] for e in await events(pool, plan))
    assert not published  # a normal trip raises no deviation


async def test_one_bad_fix_is_noise_but_a_run_of_them_is_a_deviation(world, published):  # noqa: F811
    pool = world["pool"]
    await run_to_dispatch(world)
    route = await active_route(pool)
    monitor = RouteMonitor(world["deps"])
    on_route = (route["stops"][0]["lon"], route["stops"][0]["lat"])

    await monitor.on_telemetry(await fix(pool, route["vehicle_id"], OFF_ROUTE))
    await monitor.on_telemetry(await fix(pool, route["vehicle_id"], on_route))  # back on the road: streak resets
    await monitor.on_telemetry(await fix(pool, route["vehicle_id"], OFF_ROUTE))
    await monitor.on_telemetry(await fix(pool, route["vehicle_id"], OFF_ROUTE))
    assert not published

    await monitor.on_telemetry(await fix(pool, route["vehicle_id"], OFF_ROUTE))
    assert [p.kind.value for t, p in published if t == VEHICLE_DEVIATION] == ["off_route"]
    for _ in range(4):  # still off route: the open deviation stops a second replan
        await monitor.on_telemetry(await fix(pool, route["vehicle_id"], OFF_ROUTE))
    assert len(published) == 1


async def test_poor_gps_accuracy_is_not_a_deviation(world, published):  # noqa: F811
    pool = world["pool"]
    await run_to_dispatch(world)
    route = await active_route(pool)
    monitor = RouteMonitor(world["deps"])
    for _ in range(4):
        await monitor.on_telemetry(await fix(pool, route["vehicle_id"], OFF_ROUTE, accuracy=50_000))
    assert not published


async def test_off_route_truck_keeps_its_load_and_gets_a_new_route(world, published):  # noqa: F811
    pool, plan = world["pool"], world["plan"]
    await run_to_dispatch(world)
    route = await active_route(pool)
    monitor = RouteMonitor(world["deps"])
    pickup = route["stops"][0]
    await monitor.on_telemetry(await fix(pool, route["vehicle_id"], (pickup["lon"], pickup["lat"])))
    reserved_before = await pool.fetchval("SELECT sum(reserved) FROM v_available_stock")
    for _ in range(3):
        await monitor.on_telemetry(await fix(pool, route["vehicle_id"], OFF_ROUTE))

    child = await replan_from(world, published)

    assert await pool.fetchval("SELECT status::text FROM plans WHERE id = $1", child) == "dispatched"  # no human needed
    assert await pool.fetchval("SELECT status::text FROM routes WHERE id = $1", route["id"]) == "abandoned"
    new = await pool.fetchrow("SELECT vehicle_id, stops, instructions FROM routes WHERE plan_id = $1", child)
    assert new["vehicle_id"] == route["vehicle_id"]
    picked = {k for s in new["stops"] if s["kind"] == "pickup" for k in s["order_keys"]}
    assert any(k not in picked for s in new["stops"] if s["kind"] == "dropoff" for k in s["order_keys"])  # delivered from the truck
    assert "NEW ROUTE" in new["instructions"]["source_text"]
    assert await pool.fetchval("SELECT count(*) FROM orders WHERE plan_id = $1 AND status = 'in_transit'", child) >= 1
    assert await pool.fetchval("SELECT count(*) FROM orders WHERE plan_id = $1 AND status <> 'cancelled'", plan) == 0
    # Every unit moved is either reserved by the new plan or shown to a human: never lost, never counted twice.
    held_back = await pool.fetchval("SELECT escalations FROM plans WHERE id = $1", child)
    reserved_after = await pool.fetchval("SELECT sum(reserved) FROM v_available_stock")
    assert reserved_after + sum(e["quantity_short"] for e in held_back) == pytest.approx(reserved_before)
    assert await pool.fetchval("SELECT count(*) FROM route_deviations WHERE resolved_at IS NULL") == 0
    assert await pool.fetchval("SELECT replan_id::text FROM route_deviations") == child
    nodes = [e["node"] for e in await events(pool, child)]
    assert "replanner" in nodes and "critic" in nodes and "dispatcher" in nodes and "sentinel" not in nodes
    assert await pool.fetchval("SELECT status::text FROM plans WHERE id = $1", plan) == "superseded"


async def test_offline_driver_load_is_reassigned_to_another_vehicle(world, published):  # noqa: F811
    pool = world["pool"]
    await run_to_dispatch(world)
    route = await active_route(pool)
    spare = await pool.fetchval(
        """INSERT INTO vehicles (label, driver_phone, driver_language, capacity_kg, last_position, last_seen_at)
           VALUES ('AP-T2', '+919000000011', 'en', 6000, ST_SetSRID(ST_MakePoint(80.76, 16.53), 4326), now()) RETURNING id""")
    await pool.execute("UPDATE routes SET status = 'active' WHERE id = $1", route["id"])
    await pool.execute("UPDATE vehicles SET last_seen_at = now() - interval '1 hour' WHERE id = $1", route["vehicle_id"])

    await RouteMonitor(world["deps"]).sweep()
    assert [p.kind.value for t, p in published if t == VEHICLE_DEVIATION] == ["offline"]
    child = await replan_from(world, published)

    assert await pool.fetchval("SELECT status::text FROM plans WHERE id = $1", child) == "dispatched"
    new = await pool.fetchrow("SELECT vehicle_id, stops FROM routes WHERE plan_id = $1", child)
    assert new["vehicle_id"] == spare and new["stops"][0]["kind"] == "pickup"  # nothing was loaded yet


async def test_goods_on_a_lost_truck_are_escalated_not_replanned(world, published):  # noqa: F811
    pool = world["pool"]
    await run_to_dispatch(world)
    route = await active_route(pool)
    pickup = route["stops"][0]
    await RouteMonitor(world["deps"]).on_telemetry(await fix(pool, route["vehicle_id"], (pickup["lon"], pickup["lat"])))
    await pool.execute("UPDATE vehicles SET last_seen_at = now() - interval '1 hour' WHERE id = $1", route["vehicle_id"])

    await RouteMonitor(world["deps"]).sweep()
    child = await replan_from(world, published)

    # This truck was the only vehicle, so goods still at the supplier have no carrier either. Both kinds are
    # shown to a human, with different instructions.
    row = await pool.fetchrow("SELECT status::text, unreachable_reason, escalations FROM plans WHERE id = $1", child)
    assert row["status"] == "failed" and "no online vehicle" in row["unreachable_reason"]
    reasons = [e["reason"] for e in row["escalations"]]
    assert any("last known position" in r for r in reasons) and any("no longer on a route" in r for r in reasons)
    # The goods stay on the parent's orders: they are still on that truck.
    assert await pool.fetchval("SELECT count(*) FROM orders WHERE status = 'in_transit' AND plan_id = $1", world["plan"]) >= 1


async def test_hazard_growing_across_the_road_ahead_reroutes_around_it(world, published):  # noqa: F811
    pool = world["pool"]
    await run_to_dispatch(world)
    route = await active_route(pool)
    start = (route["stops"][0]["lon"], route["stops"][0]["lat"])
    await pool.execute("UPDATE vehicles SET last_position = ST_SetSRID(ST_MakePoint($1, $2), 4326), last_seen_at = now()", *start)
    mid = await pool.fetchrow(
        "SELECT ST_X(p) AS lon, ST_Y(p) AS lat FROM (SELECT ST_LineInterpolatePoint(geom, 0.6) AS p FROM routes WHERE id = $1) x",
        route["id"])
    box = (mid["lon"] - 0.004, mid["lat"] - 0.004, mid["lon"] + 0.004, mid["lat"] + 0.004)
    new_hazard = await pool.fetchval(
        """INSERT INTO hazards (source, source_ref, dedupe_key, hazard_type, severity, headline, geom, fetched_at, expires_at)
           VALUES ('drill', 'drill:grow', 'drill:grow', 'flood', 'severe', 'DRILL: spreading', ST_Multi(ST_MakeEnvelope($1, $2, $3, $4, 4326)),
                   now(), now() + interval '2 hours') RETURNING id""", *box)

    await RouteMonitor(world["deps"]).on_hazard(HazardUpdated(
        hazard_id=UUID(str(new_hazard)), change="created", source="drill", hazard_type="flood", severity="severe", version=1,
        bbox=box, area_km2=1.0, is_drill=True))
    assert [p.kind.value for t, p in published if t == VEHICLE_DEVIATION] == ["hazard_on_route"]
    child = await replan_from(world, published)

    assert await pool.fetchval("SELECT status::text FROM plans WHERE id = $1", child) == "dispatched"
    crossing = await pool.fetchval(
        "SELECT count(*) FROM routes r, hazards h WHERE r.plan_id = $1 AND h.id = $2 AND ST_Intersects(r.geom, h.geom)",
        child, new_hazard)
    assert crossing == 0 and UUID(str(new_hazard)) in await pool.fetchval("SELECT hazard_ids FROM plans WHERE id = $1", child)


async def test_loading_at_a_stop_is_not_stalling_but_stopping_on_the_road_is(world, published):  # noqa: F811
    pool = world["pool"]
    await run_to_dispatch(world)
    route = await active_route(pool)
    monitor = RouteMonitor(world["deps"])
    first = (route["stops"][0]["lon"], route["stops"][0]["lat"])
    await monitor.on_telemetry(await fix(pool, route["vehicle_id"], first))
    long_ago = datetime.now(UTC) - timedelta(hours=1)

    monitor.anchor[str(route["vehicle_id"])] = (first, long_ago)
    await monitor.sweep()
    assert not published  # parked at the depot, loading

    mid = await pool.fetchrow(
        "SELECT ST_X(p) AS lon, ST_Y(p) AS lat FROM (SELECT ST_LineInterpolatePoint(geom, 0.5) AS p FROM routes WHERE id = $1) x",
        route["id"])
    monitor.anchor[str(route["vehicle_id"])] = ((mid["lon"], mid["lat"]), long_ago)
    await monitor.sweep()
    assert [p.kind.value for t, p in published if t == VEHICLE_DEVIATION] == ["stalled"]


async def test_a_phone_that_dies_before_its_first_fix_is_still_noticed(world, published):  # noqa: F811
    pool = world["pool"]
    await run_to_dispatch(world)
    route = await active_route(pool)
    assert route["status"] == "planned"
    await pool.execute("UPDATE vehicles SET last_seen_at = now() - interval '1 hour' WHERE id = $1", route["vehicle_id"])
    await RouteMonitor(world["deps"]).sweep()
    assert [p.kind.value for t, p in published if t == VEHICLE_DEVIATION] == ["offline"]


async def test_old_replayed_fixes_never_raise_a_deviation(world, published):  # noqa: F811
    pool = world["pool"]
    await run_to_dispatch(world)
    route = await active_route(pool)
    monitor = RouteMonitor(world["deps"])
    stale = VehicleTelemetry(telemetry_id=1, vehicle_id=route["vehicle_id"], position=LonLat(lon=OFF_ROUTE[0], lat=OFF_ROUTE[1]),
                             recorded_at=datetime.now(UTC) - timedelta(hours=1))  # fmt: skip
    for _ in range(4):
        await monitor.on_telemetry(stale)
    assert not published
