"""Evacuation: a truck collects people just outside a zone and takes them to a safe point.

There is nothing for the agents to reason about, so this runs beside the plan graph, polling a table, but
it writes an ordinary dispatched plan and route: the Route Monitor, the driver's screen and the dashboard
then work as they do for goods.

A truck can't leave a closed zone by a road that avoids it, so people gather at a real place at the edge,
and the truck collects them there. With people on board the rule is stricter than for goods, which only
avoid closures (repository.py): the way out stays clear of every active zone, forecasts included. A zone
holding the pickup or the safe point can't be avoided, and when forecast cells leave no way round, the
route may cross those, but never a drill or a closure. What it crossed is recorded and shown. Shapely
checks the way out before any truck gets it, and a new zone across it means a new route (`reroute`).

Going round dozens of forecast cells is the heaviest thing the router is asked. So evacuations use a client
of their own: a slow answer here must never open the breaker that planning shares, which would fail every
plan as "routing service unavailable". They also keep to two searches at a time, and each level of
strictness has a time budget, because people should not wait minutes for a perfect answer.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import asyncpg
from shapely import LineString, Point, from_wkb, make_valid, unary_union
from shapely.prepared import prep

from app.agents import data
from app.agents.procurement import haversine_m
from app.core.logging import get_logger
from app.core.resilience import CircuitBreaker, SourceUnavailable
from app.routing.repository import blocks_roads, fetch_active_hazards, fetch_road_hazards
from app.routing.valhalla import Location, RouteResult, ValhallaClient, ValhallaError
from app.solvers.geometry import HazardShape, exclusion_rings, verify_route

from .audit import Audit
from .dispatcher import DRIVER_TZ

if TYPE_CHECKING:
    from .nodes import Deps

log = get_logger(__name__)
POLL_S = 10
BOARDING_S = 10 * 60
SEARCH_DEG = 0.6            # about 65 km around the zone
PICKUP_CLEAR_DEG = 0.004    # about 440 m: wider than the routing margin, or the truck could not reach it
SAFE_CLEAR_DEG = 0.045      # about 5 km: a safe point is not simply the next building along
AREA_MARGIN_DEG = 0.5
PICKUP_KINDS = ["school", "shelter", "hospital", "fuel", "godown", "warehouse"]
SAFE_KINDS = ["shelter", "school", "hospital"]
PICKUPS_TRIED = 4
SAFE_POINTS_TRIED = 5
TRUCKS_TRIED = 5
TRUCK_REACH_M = 300_000  # Valhalla refuses a matrix with a pair over 400 km by road
LOST_VEHICLE_KINDS = ("offline", "stalled")
ROUTER_TIMEOUT_S = 12
SEARCHES_AT_ONCE = 2
WAY_OUT_BUDGET_S = 15       # for one start point, with its safe points side by side
LEVEL_BUDGET_S = 20         # past this, stop trying pickups at this level of strictness


@dataclass(frozen=True)
class Spot:
    facility_id: str
    name: str
    lon: float
    lat: float

    @property
    def location(self) -> Location:
        return Location(lon=self.lon, lat=self.lat)


@dataclass(frozen=True)
class Level:
    """How hard to try, strictest first."""

    outside_every_zone: bool  # the pickup and safe point are outside every active zone, not just closures
    avoid_every_zone: bool    # the way out avoids forecasts too, not just closures


LEVELS = (Level(True, True), Level(False, True), Level(False, False))


@dataclass
class WayOut:
    safe: Spot
    route: RouteResult
    clearance_m: float | None
    avoided: int
    crossed: int


ZONES_SQL = """SELECT h.id = $1 AS target, ST_AsBinary(h.geom) AS wkb, h.is_drill, h.hazard_type::text AS hazard_type,
                      h.severity::text AS severity
               FROM v_active_hazards h
               WHERE ST_DWithin(h.geom, COALESCE(ST_SetSRID(ST_MakePoint($3::float8, $4::float8), 4326),
                                                 (SELECT t.geom FROM v_active_hazards t WHERE t.id = $1)), $2::float8)"""
SPOT_SQL = """SELECT f.id::text AS facility_id, f.name, ST_X(f.geom) AS lon, ST_Y(f.geom) AS lat FROM v_facilities f
              WHERE f.kind::text = ANY($1) AND f.geom && ST_MakeEnvelope($2, $3, $4, $5, 4326)"""


async def _spots(pool: asyncpg.Pool, hazard_id: str, outside_every_zone: bool, kinds: list[str], clear_deg: float, limit: int,
                 near: tuple[float, float] | None = None) -> list[Spot]:
    """Real places clear of the zones, nearest to this zone first, or to `near` for a truck already on the
    road. Checked here with a prepared shape: in SQL, testing thousands of facilities against 18,000
    vertices of live alerts takes over 30 seconds."""
    zones = await pool.fetch(ZONES_SQL, hazard_id, SEARCH_DEG, *(near or (None, None)))
    if not zones and not near:
        return []
    danger = unary_union([make_valid(from_wkb(z["wkb"])) for z in zones
                          if outside_every_zone or blocks_roads(z["hazard_type"], z["severity"], z["is_drill"])])  # fmt: skip
    target = Point(near) if near else make_valid(from_wkb(next(z["wkb"] for z in zones if z["target"])))
    x0, y0, x1, y1 = target.bounds
    rows = await pool.fetch(SPOT_SQL, kinds, x0 - SEARCH_DEG, y0 - SEARCH_DEG, x1 + SEARCH_DEG, y1 + SEARCH_DEG)
    unsafe = prep(danger.buffer(clear_deg))
    spots = [Spot(**dict(r)) for r in rows if not unsafe.contains(Point(r["lon"], r["lat"]))]
    rough = target.simplify(0.01)  # good enough to rank by, and much faster than every vertex
    return sorted(spots, key=lambda spot: rough.distance(Point(spot.lon, spot.lat)))[:limit]


def _router(deps: "Deps") -> ValhallaClient:
    """One per worker. No retries: a search that timed out once will time out again."""
    if deps.evacuation_router is None:
        deps.evacuation_router = ValhallaClient(deps.settings.valhalla_url, deps.valhalla.client, retries=0, timeout_s=ROUTER_TIMEOUT_S,
                                                breaker=CircuitBreaker("valhalla_evacuation", failure_threshold=20, reset_after_s=30))  # fmt: skip
        deps.evacuation_searches = asyncio.Semaphore(SEARCHES_AT_ONCE)
    return deps.evacuation_router


def _area(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    lons, lats = [p[0] for p in points], [p[1] for p in points]
    return min(lons) - AREA_MARGIN_DEG, min(lats) - AREA_MARGIN_DEG, max(lons) + AREA_MARGIN_DEG, max(lats) + AREA_MARGIN_DEG


async def way_out(deps: "Deps", start: tuple[float, float], safe: Spot, avoid_every_zone: bool) -> WayOut | None:
    """A checked road from `start` to `safe`, or None. Every zone near it counts, not only closures."""
    area = _area([start, (safe.lon, safe.lat)])
    zones = await fetch_active_hazards(deps.pool, bbox=area)
    closures = {h.hazard_id for h in await fetch_road_hazards(deps.pool, area, [])}
    ends = [Point(start), Point(safe.lon, safe.lat)]
    if any(h.hazard_id in closures and h.geom.contains(ends[1]) for h in zones):
        return None
    # A zone holding either end can't be avoided: a truck caught inside one has to drive out of it.
    avoid: list[HazardShape] = [h for h in zones if not any(h.geom.contains(end) for end in ends)
                                and (avoid_every_zone or h.hazard_id in closures)]  # fmt: skip
    router = _router(deps)
    try:
        async with deps.evacuation_searches:
            route = await router.route([Location(lon=start[0], lat=start[1]), safe.location], exclude=exclusion_rings(avoid))
    except (ValhallaError, SourceUnavailable):  # too slow counts as no way found: the next level is easier
        return None
    verdict = verify_route(route.shape, avoid)
    if not verdict.passed:
        return None
    kept = {h.hazard_id for h in avoid}
    crossed = verify_route(route.shape, [h for h in zones if h.hazard_id not in kept]).violations
    return WayOut(safe=safe, route=route, clearance_m=verdict.min_clearance_m, avoided=len(avoid), crossed=len(crossed))


async def _nearest_truck(deps: "Deps", hazard_id: str, pickup: Spot) -> tuple[dict, RouteResult] | None:
    """The free truck with the shortest road to the pickup that stays out of closures. By road, not by eye: a
    zone can cut a town in two, and the truck nearest in a straight line can have no way across."""
    rows = [dict(r) for r in await data.usable_vehicles(deps.pool, [hazard_id])
            if haversine_m(r["lon"], r["lat"], pickup.lon, pickup.lat) <= TRUCK_REACH_M]  # fmt: skip
    if not rows:
        return None
    closures = await fetch_road_hazards(deps.pool, _area([(r["lon"], r["lat"]) for r in rows] + [(pickup.lon, pickup.lat)]), [])
    rings = exclusion_rings(closures)
    starts = [Location(lon=r["lon"], lat=r["lat"]) for r in rows]
    router = _router(deps)
    try:
        async with deps.evacuation_searches:
            times = (await router.matrix(starts, [pickup.location], exclude=rings)).durations_s
    except (ValhallaError, SourceUnavailable):
        return None
    reachable = sorted((row[0], i) for i, row in enumerate(times) if row[0] is not None)
    for _, i in reachable[:TRUCKS_TRIED]:
        try:
            async with deps.evacuation_searches:
                return rows[i], await router.route([starts[i], pickup.location], exclude=rings)
        except (ValhallaError, SourceUnavailable):
            continue
    return None


def _stop(spot: Spot, kind: str, at: datetime) -> dict:
    return {"facility_id": spot.facility_id, "name": spot.name, "kind": kind, "eta": at.isoformat(), "order_keys": [],
            "lon": spot.lon, "lat": spot.lat}  # fmt: skip


def _instructions(label: str, people: int, settlement: str, pickup: Spot | None, pickup_at: datetime | None, way: WayOut,
                  safe_at: datetime, drill: bool) -> dict:
    lines = [("DRILL - NOT A REAL DISPATCH. " if drill else "") + f"EVACUATION. Vehicle {label}."]
    if pickup:
        lines.append(f"1. Pick up {people} people from {settlement} at {pickup.name} by {pickup_at.astimezone(DRIVER_TZ):%H:%M} IST.")
    lines.append(f"{2 if pickup else 1}. Take them to {way.safe.name} by {safe_at.astimezone(DRIVER_TZ):%H:%M} IST.")
    lines.append("The way out avoids the active hazard zones; do not take shortcuts.")
    text = "\n".join(lines)
    return {"language": "en", "text": text, "source_text": text}


async def assign(deps: "Deps", evacuation: asyncpg.Record) -> None:
    pool, hazard_id = deps.pool, str(evacuation["hazard_id"])
    facts = await data.hazard_facts(pool, [hazard_id])
    if not facts:
        await _fail(pool, evacuation["id"], "the zone ended before a truck was sent")
        return
    if not await data.usable_vehicles(pool, [hazard_id]):
        return  # every truck is busy: it stays waiting, and the next round tries again
    for level in LEVELS:
        pickups = await _spots(pool, hazard_id, level.outside_every_zone, PICKUP_KINDS, PICKUP_CLEAR_DEG, PICKUPS_TRIED)
        safes = await _spots(pool, hazard_id, level.outside_every_zone, SAFE_KINDS, SAFE_CLEAR_DEG, SAFE_POINTS_TRIED)
        out_of_time = time.monotonic() + LEVEL_BUDGET_S
        for pickup in pickups:
            if time.monotonic() > out_of_time:
                break
            found = await _first_way_out(deps, (pickup.lon, pickup.lat), safes, level.avoid_every_zone)
            if found is None:
                continue
            truck = await _nearest_truck(deps, hazard_id, pickup)
            if truck is None:
                continue  # the zone cuts this pickup off from every truck: try another side of it
            await _dispatch(deps, evacuation, facts[0].is_drill, pickup, found, *truck)
            return
    await deps.valhalla.status()  # if the router itself is down, this raises and the request stays waiting: "no road" would be false
    await _fail(pool, evacuation["id"], "no truck can reach this zone and leave it by a safe road: send help another way")


async def _first_way_out(deps: "Deps", start: tuple[float, float], safes: list[Spot], avoid_every_zone: bool) -> WayOut | None:
    """Side by side: the router can take 8 seconds to say there is no path, and a truck may be waiting on the answer."""
    _router(deps)
    try:
        found = await asyncio.wait_for(asyncio.gather(*(way_out(deps, start, safe, avoid_every_zone) for safe in safes)), WAY_OUT_BUDGET_S)
    except TimeoutError:
        return None
    return next((way for way in found if way), None)


async def _dispatch(deps: "Deps", e: asyncpg.Record, drill: bool, pickup: Spot, way: WayOut, truck: dict, way_in: RouteResult) -> None:
    now = datetime.now(UTC)
    pickup_at = now + timedelta(seconds=way_in.duration_s)
    safe_at = pickup_at + timedelta(seconds=BOARDING_S + way.route.duration_s)
    shape = [*way_in.shape, *way.route.shape[1:]]
    summary = (f"Evacuation{' (simulated request)' if e['simulated'] else ''}: {e['people']} people from {e['settlement']}. "
               f"Truck {truck['label']} collects them at {pickup.name} and takes them to {way.safe.name}. "
               f"The way out avoids {way.avoided} active zone(s)" + (f" and crosses {way.crossed} it could not get round." if way.crossed else "."))
    stops = [_stop(pickup, "pickup", pickup_at), _stop(way.safe, "dropoff", safe_at)]
    instructions = _instructions(truck["label"], e["people"], e["settlement"], pickup, pickup_at, way, safe_at, drill)
    pool, thread = deps.pool, str(uuid4())
    async with pool.acquire() as conn, conn.transaction():
        plan_id = await conn.fetchval(
            """INSERT INTO plans (thread_id, trigger_kind, trigger_ref, hazard_ids, status, is_drill, requires_approval, summary, requested_by)
               VALUES ($1, 'manual', $2, $3::uuid[], 'dispatched', $4, false, $5, $6) RETURNING id""",
            thread, f"evacuation:{e['id']}", [e["hazard_id"]], drill, summary, e["requested_by"])  # fmt: skip
        route_id = await _insert_route(conn, plan_id, truck["id"], shape, way_in.distance_m + way.route.distance_m,
                                       way_in.duration_s + way.route.duration_s, stops, way.clearance_m, instructions)  # fmt: skip
        await conn.execute(
            """UPDATE evacuations SET status = 'assigned', plan_id = $2, route_id = $3, pickup_name = $4, safe_name = $5,
                   zones_avoided = $6, zones_crossed = $7, updated_at = now() WHERE id = $1""",
            e["id"], plan_id, route_id, pickup.name, way.safe.name, way.avoided, way.crossed)  # fmt: skip
    await Audit(pool, str(plan_id), thread)("dispatcher", "dispatch", summary, {"evacuation": str(e["id"])},
                                            [f"route:{route_id}", f"vehicle:{truck['id']}", f"hazard:{e['hazard_id']}"])  # fmt: skip
    log.info("evacuation_dispatched", evacuation=str(e["id"]), route=str(route_id))


async def _insert_route(conn, plan_id, vehicle_id, shape, distance_m, duration_s, stops, clearance_m, instructions) -> UUID:
    return await conn.fetchval(
        """INSERT INTO routes (plan_id, vehicle_id, kind, status, geom, distance_m, duration_s, stops, load_kg, min_hazard_clearance_m,
                               instructions)
           VALUES ($1, $2, 'evacuation', 'planned', ST_SetSRID(ST_GeomFromText($3), 4326), $4, $5, $6, 0, $7, $8) RETURNING id""",
        plan_id, vehicle_id, LineString(shape).wkt, distance_m, duration_s, json.loads(json.dumps(stops)), clearance_m, instructions)  # fmt: skip


async def _fail(pool: asyncpg.Pool, evacuation_id, reason: str) -> None:
    await pool.execute("UPDATE evacuations SET status = 'failed', failure = $2, updated_at = now() WHERE id = $1", evacuation_id, reason)
    log.warning("evacuation_failed", evacuation=str(evacuation_id), reason=reason)


async def reroute(deps: "Deps", kind: str, route_id: UUID, deviation_id: UUID) -> bool:
    """A deviation on an evacuation route. There are no goods to replan, so it is settled here: True if it
    was one. People not yet on board wait for another truck. People on board get a new way out from where
    the truck is now, unless the truck is lost, which only a person can sort out."""
    pool = deps.pool
    e = await pool.fetchrow(
        """SELECT e.*, r.completed_stops, r.stops, r.vehicle_id, r.plan_id AS route_plan, p.thread_id, p.is_drill,
                  ST_X(v.last_position) AS lon, ST_Y(v.last_position) AS lat, v.label
           FROM evacuations e JOIN routes r ON r.id = e.route_id JOIN plans p ON p.id = r.plan_id
           JOIN vehicles v ON v.id = r.vehicle_id WHERE e.route_id = $1""", route_id)  # fmt: skip
    if e is None:
        return False
    audit = Audit(pool, str(e["route_plan"]), e["thread_id"])

    async def settle(conn) -> None:
        """Last, and together with whatever replaces the route. The truck keeps its route until then, and
        a crash before this leaves the deviation open, so the sweep tries again."""
        await conn.execute("UPDATE routes SET status = 'abandoned', updated_at = now() WHERE id = $1", route_id)
        await conn.execute("UPDATE route_deviations SET resolved_at = now() WHERE id = $1", deviation_id)

    async def give_up(reason: str) -> None:
        async with pool.acquire() as conn, conn.transaction():
            await settle(conn)
            await conn.execute("UPDATE evacuations SET status = 'failed', failure = $2, updated_at = now() WHERE id = $1", e["id"], reason)
        await audit("replanner", "error", reason)

    if e["completed_stops"] == 0:
        async with pool.acquire() as conn, conn.transaction():
            await settle(conn)
            await conn.execute("UPDATE evacuations SET status = 'waiting', route_id = NULL, updated_at = now() WHERE id = $1", e["id"])
        await audit("replanner", "decision", f"Evacuation: {e['label']} can't reach the pickup ({kind.replace('_', ' ')}). Finding another truck")
        return True
    if kind in LOST_VEHICLE_KINDS:
        await give_up(f"{e['label']} was lost with {e['people']} people on board ({kind}): call the driver")
        return True
    start = (e["lon"], e["lat"])
    last = e["stops"][-1]
    planned = Spot(last["facility_id"], last["name"], last["lon"], last["lat"])
    for level in LEVELS:
        near = await _spots(pool, str(e["hazard_id"]), level.outside_every_zone, SAFE_KINDS, SAFE_CLEAR_DEG, SAFE_POINTS_TRIED, near=start)
        found = await _first_way_out(deps, start, [planned, *near], level.avoid_every_zone)
        if found is None:
            continue
        safe_at = datetime.now(UTC) + timedelta(seconds=found.route.duration_s)
        instructions = _instructions(e["label"], e["people"], e["settlement"], None, None, found, safe_at, e["is_drill"])
        async with pool.acquire() as conn, conn.transaction():
            await settle(conn)
            new_route = await _insert_route(conn, e["route_plan"], e["vehicle_id"], found.route.shape, found.route.distance_m,
                                            found.route.duration_s, [_stop(found.safe, "dropoff", safe_at)], found.clearance_m, instructions)  # fmt: skip
            await conn.execute(
                """UPDATE evacuations SET route_id = $2, safe_name = $3, zones_avoided = $4, zones_crossed = $5, updated_at = now()
                   WHERE id = $1""", e["id"], new_route, found.safe.name, found.avoided, found.crossed)  # fmt: skip
        await audit("replanner", "decision", f"Evacuation rerouted: {e['label']} takes {e['people']} people to {found.safe.name}, "
                    f"avoiding {found.avoided} active zone(s)", {"kind": kind}, [f"route:{new_route}"])  # fmt: skip
        return True
    await deps.valhalla.status()  # if the router is down, this raises, the deviation stays open and the sweep tries again
    await give_up(f"no safe road left for {e['label']} with {e['people']} people on board: call the driver")
    return True


async def run(deps: "Deps") -> None:
    while True:
        try:
            for evacuation in await deps.pool.fetch("SELECT * FROM evacuations WHERE status = 'waiting' ORDER BY created_at"):
                await assign(deps, evacuation)
        except SourceUnavailable as exc:  # the router is down: the requests stay waiting, nobody is told a false "no road"
            log.warning("evacuation_waiting_for_routing", error=str(exc))
        except Exception:  # a failed round must not end evacuations
            log.exception("evacuation_round_failed")
        await asyncio.sleep(POLL_S)
