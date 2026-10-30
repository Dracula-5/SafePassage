"""Closed loop: watches dispatched routes against live telemetry and hazards.

Deterministic, no LLM. Each detection is written to route_deviations (one open row per route and
kind, so GPS noise cannot start two replans), then published as vehicle.deviation. The handler for
that event asks for a replacement plan, which is what makes the loop restart-safe: a deviation
recorded just before a crash is redelivered, and the plan's idempotency key stops a duplicate.

  off_route        fixes stay farther from the route than the threshold, several in a row
  stalled          no real movement for a while, away from any stop
  offline          no fix for longer than the grace period (v_vehicle_status)
  hazard_on_route  a new or grown hazard crosses the road still ahead
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from reliefroute_contracts.events import EventEnvelope, HazardUpdated, LonLat, VehicleDeviation, VehicleTelemetry
from reliefroute_contracts.streams import HAZARD_UPDATED, VEHICLE_DEVIATION, VEHICLE_TELEMETRY

from app.core import bus
from app.core.logging import get_logger
from app.routing.repository import fetch_active_hazards
from app.solvers.deviation import distance_from_route_m, distance_m, remaining_route
from app.solvers.geometry import LonLat as Point, verify_route

from . import plans
from .audit import Audit

log = get_logger(__name__)
MAX_FIX_AGE = timedelta(minutes=5)   # replayed offline-queue fixes describe the past, not the present
MOVED_M = 50.0                       # GPS jitter below this is not movement

TRACKED_SQL = """
SELECT a.route_id::text, a.plan_id::text, a.vehicle_id::text, a.route_status::text, a.stops, a.is_drill, p.thread_id,
       r.completed_stops, ST_AsGeoJSON(a.geom) AS geojson, v.label, v.is_offline, v.seconds_since_seen,
       ST_X(v.last_position) AS lon, ST_Y(v.last_position) AS lat
FROM v_vehicle_assignment a
JOIN routes r ON r.id = a.route_id JOIN plans p ON p.id = a.plan_id JOIN v_vehicle_status v ON v.id = a.vehicle_id
"""


@dataclass
class Tracked:
    route_id: str
    plan_id: str
    vehicle_id: str
    thread_id: str
    label: str
    status: str
    stops: list[dict]
    completed: int
    geometry: list[Point]
    position: Point | None
    is_offline: bool
    seconds_since_seen: float | None

    @property
    def next_stop(self) -> dict | None:
        return self.stops[self.completed] if self.completed < len(self.stops) else None


def _tracked(row) -> Tracked:
    return Tracked(route_id=row["route_id"], plan_id=row["plan_id"], vehicle_id=row["vehicle_id"], thread_id=row["thread_id"],
                   label=row["label"], status=row["route_status"], stops=row["stops"], completed=row["completed_stops"],
                   geometry=[tuple(c) for c in json.loads(row["geojson"])["coordinates"]],
                   position=None if row["lon"] is None else (row["lon"], row["lat"]),
                   is_offline=row["is_offline"], seconds_since_seen=row["seconds_since_seen"])  # fmt: skip


class RouteMonitor:
    def __init__(self, deps):
        self.deps = deps
        self.s = deps.settings
        self.off_route_streak: dict[str, int] = {}
        self.anchor: dict[str, tuple[Point, datetime]] = {}  # last real movement per vehicle

    async def handle(self, envelope: EventEnvelope) -> None:
        if envelope.event_type == VEHICLE_TELEMETRY:
            await self.on_telemetry(envelope.payload)
        elif envelope.event_type == HAZARD_UPDATED:
            await self.on_hazard(envelope.payload)
        elif envelope.event_type == VEHICLE_DEVIATION:
            await self.on_deviation(envelope.payload)

    async def run_sweeps(self) -> None:
        while True:
            try:
                await self.sweep()
            except Exception:  # a failed sweep must not end monitoring
                log.exception("monitor_sweep_failed")
            await asyncio.sleep(self.s.monitor_sweep_s)

    async def _active(self, vehicle_id: str | None = None) -> list[Tracked]:
        rows = await self.deps.pool.fetch(TRACKED_SQL + " WHERE $1::uuid IS NULL OR a.vehicle_id = $1", vehicle_id)
        return [_tracked(r) for r in rows]

    # ---------------------------------------------------------------- telemetry
    async def on_telemetry(self, fix: VehicleTelemetry) -> None:
        if datetime.now(UTC) - fix.recorded_at > MAX_FIX_AGE:
            return
        for route in await self._active(str(fix.vehicle_id)):
            point = (fix.position.lon, fix.position.lat)
            self._note_movement(route.vehicle_id, point, fix.recorded_at)
            if route.status == "planned":
                await self.deps.pool.execute(
                    "UPDATE routes SET status = 'active', started_at = now(), updated_at = now() WHERE id = $1", route.route_id)
            await self._advance(route, point)
            if route.next_stop is None:
                continue
            away = distance_from_route_m(route.geometry, point)
            if away <= max(self.s.off_route_m, fix.accuracy_m or 0):
                self.off_route_streak.pop(route.route_id, None)
                continue
            streak = self.off_route_streak.get(route.route_id, 0) + 1
            self.off_route_streak[route.route_id] = streak
            if streak >= self.s.off_route_fixes:
                self.off_route_streak.pop(route.route_id, None)
                await self._deviate(route, "off_route", point, distance=away)

    def _note_movement(self, vehicle_id: str, point: Point, at: datetime) -> None:
        anchor = self.anchor.get(vehicle_id)
        if anchor is None or distance_m(anchor[0], point) > MOVED_M:
            self.anchor[vehicle_id] = (point, at)

    async def _advance(self, route: Tracked, point: Point) -> None:
        """Count every stop the vehicle has reached; several stops can share one site."""
        reached = []
        while (stop := route.next_stop) and distance_m((stop["lon"], stop["lat"]), point) <= self.s.arrival_radius_m:
            reached.append(stop)
            route.completed += 1
        if not reached:
            return
        pool = self.deps.pool
        finished = route.next_stop is None
        await pool.execute(
            """UPDATE routes SET completed_stops = $2, updated_at = now(),
                   status = CASE WHEN $3 THEN 'completed'::route_status ELSE status END,
                   completed_at = CASE WHEN $3 THEN now() ELSE completed_at END
               WHERE id = $1""", route.route_id, route.completed, finished)  # fmt: skip
        await self._sync_orders(route.plan_id)
        names = ", ".join(f"{s['kind']} {s['name']}" for s in reached)
        await Audit(pool, route.plan_id, route.thread_id)(
            "replanner", "decision", f"{route.label} reached {names} ({route.completed}/{len(route.stops)} stops)"
            + (": route complete" if finished else ""), {"route_id": route.route_id, "position": point},
            [f"route:{route.route_id}", f"vehicle:{route.vehicle_id}"])

    async def _sync_orders(self, plan_id: str) -> None:
        """Order status follows the stops of every route of the plan: a split order is delivered
        only when all its parts are."""
        pool = self.deps.pool
        routes = await pool.fetch("SELECT stops, completed_stops FROM routes WHERE plan_id = $1 AND status <> 'abandoned'", plan_id)
        all_keys, picked, dropped = set(), set(), set()
        for r in routes:
            for i, stop in enumerate(r["stops"]):
                reached = i < r["completed_stops"]
                if stop["kind"] == "dropoff":
                    all_keys.update(stop["order_keys"])
                    if reached:
                        dropped.update(stop["order_keys"])
                elif reached:
                    picked.update(k.split("#")[0] for k in stop["order_keys"])
        allocation = await pool.fetchval("SELECT allocation FROM plans WHERE id = $1", plan_id)
        for order in (allocation or {}).get("orders", []):
            key = order["order_key"]
            parts = {k for k in all_keys if k.split("#")[0] == key}
            match = (plan_id, order["supplier_id"], order["destination_facility_id"])
            if parts and parts <= dropped:
                await pool.execute(
                    """UPDATE orders SET status = 'delivered', delivered_at = now(), updated_at = now()
                       WHERE plan_id = $1 AND supplier_id = $2 AND destination_facility_id = $3
                         AND status IN ('dispatched', 'in_transit')""", *match)  # fmt: skip
            elif key in picked:
                await pool.execute(
                    """UPDATE orders SET status = 'in_transit', updated_at = now()
                       WHERE plan_id = $1 AND supplier_id = $2 AND destination_facility_id = $3
                         AND status = 'dispatched'""", *match)  # fmt: skip

    # ---------------------------------------------------------------- hazards and sweeps
    async def on_hazard(self, hazard: HazardUpdated) -> None:
        if hazard.change not in ("created", "updated"):
            return
        shapes = [h for h in await fetch_active_hazards(self.deps.pool, bbox=hazard.bbox) if h.hazard_id == str(hazard.hazard_id)]
        if not shapes:
            return
        for route in await self._active():
            ahead = remaining_route(route.geometry, route.position) if route.position else route.geometry
            if len(ahead) >= 2 and not verify_route(ahead, shapes).passed:
                await self._deviate(route, "hazard_on_route", route.position, hazard_id=str(hazard.hazard_id))

    async def sweep(self) -> None:
        await self._retry_unhandled_deviations()
        now = datetime.now(UTC)
        for route in await self._active():
            if route.status != "active" or route.next_stop is None:
                continue
            silent = route.seconds_since_seen or 0
            if route.is_offline and silent >= self.s.offline_grace_s:
                await self._deviate(route, "offline", route.position)
                continue
            anchor = self.anchor.get(route.vehicle_id)
            if anchor and (now - anchor[1]).total_seconds() >= self.s.stalled_after_s:
                await self._deviate(route, "stalled", anchor[0])

    async def _retry_unhandled_deviations(self) -> None:
        """A deviation whose event was lost to a Redis outage still gets its replan."""
        rows = await self.deps.pool.fetch(
            """SELECT route_id, plan_id, vehicle_id, kind::text, hazard_id, detected_at, distance_from_route_m
               FROM route_deviations
               WHERE resolved_at IS NULL AND replan_id IS NULL AND detected_at < now() - make_interval(secs => $1)""",
            float(self.s.monitor_sweep_s))  # fmt: skip
        for r in rows:
            await self.on_deviation(VehicleDeviation(**dict(r)))

    # ---------------------------------------------------------------- deviation -> replan
    async def _deviate(self, route: Tracked, kind: str, point: Point | None, *, distance: float | None = None,
                       hazard_id: str | None = None) -> None:
        pool = self.deps.pool
        row = await pool.fetchrow(
            """INSERT INTO route_deviations (route_id, plan_id, vehicle_id, kind, hazard_id, distance_from_route_m, last_position)
               VALUES ($1, $2, $3, $4, $5, $6,
                       CASE WHEN $7::float8 IS NULL THEN NULL ELSE ST_SetSRID(ST_MakePoint($7::float8, $8::float8), 4326) END)
               ON CONFLICT (route_id, kind) WHERE resolved_at IS NULL DO NOTHING RETURNING detected_at""",
            route.route_id, route.plan_id, route.vehicle_id, kind, hazard_id, distance,
            point[0] if point else None, point[1] if point else None,
        )  # fmt: skip
        if row is None:
            return  # already being handled
        detail = f"{distance:.0f} m from the route" if distance is not None else f"hazard {hazard_id}" if hazard_id else ""
        await Audit(pool, route.plan_id, route.thread_id)(
            "replanner", "decision", f"Deviation on {route.label}: {kind.replace('_', ' ')}" + (f" ({detail})" if detail else ""),
            {"kind": kind, "position": point, "distance_from_route_m": distance},
            [f"route:{route.route_id}", f"vehicle:{route.vehicle_id}"] + ([f"hazard:{hazard_id}"] if hazard_id else []))
        await bus.try_publish(VEHICLE_DEVIATION, VehicleDeviation(
            vehicle_id=route.vehicle_id, plan_id=route.plan_id, route_id=route.route_id, kind=kind,
            detected_at=row["detected_at"], last_position=LonLat(lon=point[0], lat=point[1]) if point else None,
            distance_from_route_m=distance, hazard_id=hazard_id,
        ), dedupe_key=f"{route.route_id}:{kind}:{row['detected_at'].isoformat()}")  # fmt: skip

    async def on_deviation(self, deviation: VehicleDeviation) -> None:
        pool = self.deps.pool
        open_id = await pool.fetchval(
            "SELECT id FROM route_deviations WHERE route_id = $1 AND kind = $2 AND resolved_at IS NULL",
            deviation.route_id, deviation.kind.value)  # fmt: skip
        if open_id is None:
            return  # already replanned
        hazard_ids = [str(r["id"]) for r in await pool.fetch(
            """SELECT h.id FROM v_active_hazards h, plans p WHERE p.id = $1 AND h.id = ANY(p.hazard_ids)""", deviation.plan_id)]
        if deviation.hazard_id and str(deviation.hazard_id) not in hazard_ids:
            hazard_ids.append(str(deviation.hazard_id))
        summary, created = await plans.request_plan(
            pool, trigger_kind="replan", trigger_ref=str(deviation.route_id), hazard_ids=hazard_ids, requested_by="replanner",
            idempotency_key=f"deviation:{open_id}", parent_plan_id=str(deviation.plan_id))  # fmt: skip
        await pool.execute("UPDATE route_deviations SET replan_id = $2 WHERE id = $1", open_id, summary["id"])
        if created:
            log.info("replan_requested", plan_id=summary["id"], route_id=str(deviation.route_id), kind=deviation.kind.value)
