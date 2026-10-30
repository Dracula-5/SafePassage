"""Route Planner: hazard-aware Valhalla matrix + OR-Tools pickup/delivery VRP + Valhalla geometry."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.routing.valhalla import Location, LocationNotRoutableError, NoRouteError, RouteResult, ValhallaClient
from app.solvers.geometry import HazardShape, exclusion_rings
from app.solvers.vrp import VrpProblem, VrpStop, VrpVehicle, solve

from .schemas import OrderDraft, OrderPart, PlannedRoute, RouteStop, Routing

MAX_ROUTE_LOCATIONS = 20   # Valhalla service_limits.truck.max_locations
SERVICE_S = 20 * 60          # loading / unloading time at each stop
MARGINS_M = (250.0, 500.0, 1000.0)  # exclusion margin grows with each Critic rejection


@dataclass
class VehicleIn:
    vehicle_id: str
    label: str
    capacity_kg: float
    driver_language: str
    lon: float
    lat: float


@dataclass
class Site:
    facility_id: str
    name: str
    lon: float
    lat: float


def split_order(order: OrderDraft, max_kg: float) -> list[tuple[str, OrderDraft]]:
    """Split an order heavier than any vehicle into equal parts that each fit (part keys: key#1, key#2...)."""
    if order.weight_kg <= max_kg:
        return [(order.order_key, order)]
    parts = math.ceil(order.weight_kg / max_kg)
    out = []
    for i in range(parts):
        part = order.model_copy(update={
            "weight_kg": order.weight_kg / parts,
            "lines": [line.model_copy(update={"quantity": line.quantity / parts}) for line in order.lines],
        })  # fmt: skip
        out.append((f"{order.order_key}#{i + 1}", part))
    return out


def first_wave(parts: list[tuple[str, OrderDraft]], budget_kg: float) -> tuple[list[tuple[str, OrderDraft]], list[str]]:
    """Take parts in rotation across orders until the fleet's wave capacity is used.

    Rotation gives every destination and supplier a share of the first wave instead of one order
    filling it. The rest is returned as part keys for the next wave.
    """
    queues: dict[str, deque[tuple[str, OrderDraft]]] = {}
    for key, order in parts:
        queues.setdefault(order.order_key, deque()).append((key, order))
    chosen: list[tuple[str, OrderDraft]] = []
    deferred: list[str] = []
    used = 0.0
    while queues:
        for order_key in list(queues):
            key, order = queues[order_key].popleft()
            if used + order.weight_kg <= budget_kg:
                chosen.append((key, order))
                used += order.weight_kg
            else:
                deferred.append(key)
            if not queues[order_key]:
                del queues[order_key]
    return chosen, deferred


async def route_in_chunks(valhalla: ValhallaClient, waypoints: list[Location], rings) -> RouteResult:
    """Valhalla caps locations per route; chain overlapping chunks and join their geometry."""
    step = MAX_ROUTE_LOCATIONS - 1
    parts = [await valhalla.route(waypoints[i : i + MAX_ROUTE_LOCATIONS], exclude=rings)
             for i in range(0, len(waypoints) - 1, step)]  # fmt: skip
    shape = [pt for i, p in enumerate(parts) for pt in (p.shape if i == 0 else p.shape[1:])]
    return RouteResult(distance_m=sum(p.distance_m for p in parts), duration_s=sum(p.duration_s for p in parts),
                       shape=shape, legs=[leg for p in parts for leg in p.legs])


async def plan_routes(
    valhalla: ValhallaClient,
    orders: list[OrderDraft],
    vehicles: list[VehicleIn],
    sites: dict[str, Site],
    hazards: list[HazardShape],
    start: datetime,
    horizon_s: int,
    attempt: int,
    max_trips: int = 3,
    shift_s: int | None = None,
    onboard_vehicle_id: str | None = None,
) -> Routing:
    margin = MARGINS_M[min(attempt, len(MARGINS_M)) - 1]
    rings = exclusion_rings(hazards, margin_m=margin, simplify_tolerance_m=margin * 0.4)
    max_cap = max(v.capacity_kg for v in vehicles)
    # Goods on the truck already fit it and must stay on it; only the rest is split and waved.
    loaded = [(o.order_key, o) for o in orders if o.onboard]
    onboard_kg = sum(o.weight_kg for _, o in loaded)
    waiting = [p for o in orders if not o.onboard for p in split_order(o, max_cap)]
    wave, deferred = first_wave(waiting, sum(v.capacity_kg for v in vehicles) * max_trips - onboard_kg)
    parts = loaded + wave

    # Matrix nodes: vehicles first, then each distinct facility used by an order.
    locations: list[Location] = [Location(lon=v.lon, lat=v.lat) for v in vehicles]
    node_of_site: dict[str, int] = {}
    for _, o in parts:
        for fid in (o.destination_facility_id,) if o.onboard else (o.supplier_facility_id, o.destination_facility_id):
            if fid not in node_of_site:
                node_of_site[fid] = len(locations)
                locations.append(Location(lon=sites[fid].lon, lat=sites[fid].lat))
    matrix = await valhalla.matrix(locations, locations, exclude=rings)

    stops, pairs = [], []
    for key, o in parts:
        if o.onboard:
            stops.append(VrpStop(stop_id=f"D:{key}", node=node_of_site[o.destination_facility_id], demand_kg=o.weight_kg,
                                 kind="unload", window_end_s=horizon_s, service_s=SERVICE_S,
                                 vehicle_id=onboard_vehicle_id))  # fmt: skip
            continue
        stops.append(VrpStop(stop_id=f"P:{key}", node=node_of_site[o.supplier_facility_id], demand_kg=o.weight_kg,
                             kind="pickup", service_s=SERVICE_S))
        stops.append(VrpStop(stop_id=f"D:{key}", node=node_of_site[o.destination_facility_id], demand_kg=o.weight_kg,
                             kind="dropoff", window_end_s=horizon_s, service_s=SERVICE_S))  # fmt: skip
        pairs.append((f"P:{key}", f"D:{key}"))
    solution = solve(VrpProblem(
        durations_s=matrix.durations_s, stops=stops, pairs=pairs, horizon_s=horizon_s, time_limit_s=5,
        vehicles=[VrpVehicle(vehicle_id=v.vehicle_id, start_node=i, end_node=i, capacity_kg=v.capacity_kg,
                             available_until_s=shift_s) for i, v in enumerate(vehicles)],
    ))  # fmt: skip

    by_vehicle = {v.vehicle_id: v for v in vehicles}
    facility_at = {node: fid for fid, node in node_of_site.items()}
    routes = []
    for r in solution.routes:
        v = by_vehicle[r.vehicle_id]
        # Geometry: start -> each stop location in visit order (consecutive repeats collapsed).
        waypoints = [locations[r.nodes[0]]]
        for visit in r.visits:
            loc = locations[visit.node]
            if loc != waypoints[-1]:
                waypoints.append(loc)
        try:
            geo = await route_in_chunks(valhalla, waypoints, rings) if len(waypoints) > 1 else None
        except (NoRouteError, LocationNotRoutableError):
            geo = None
        if geo is None:
            continue  # cannot draw a safe road for this vehicle: its orders stay unserved
        merged: list[RouteStop] = []
        for visit in r.visits:
            fid = facility_at[visit.node]
            kind = "pickup" if visit.kind == "pickup" else "dropoff"
            key = visit.stop_id.split(":", 1)[1]
            if merged and merged[-1].facility_id == fid and merged[-1].kind == kind:
                merged[-1].order_keys.append(key)
                continue
            merged.append(RouteStop(facility_id=fid, name=sites[fid].name, kind=kind,
                                    eta=start + timedelta(seconds=visit.arrival_s), order_keys=[key],
                                    lon=sites[fid].lon, lat=sites[fid].lat))  # fmt: skip
        routes.append(PlannedRoute(
            vehicle_id=v.vehicle_id, vehicle_label=v.label, driver_language=v.driver_language,
            order_keys=sorted({s.stop_id.split(":", 1)[1] for s in r.visits}), stops=merged,
            geometry=geo.shape, distance_m=geo.distance_m, duration_s=geo.duration_s,
            load_kg=r.load_kg, peak_load_kg=r.peak_load_kg, capacity_kg=v.capacity_kg,
        ))  # fmt: skip
    routed = {k for route in routes for k in route.order_keys}
    unserved = sorted({key for key, _ in parts} - routed)
    return Routing(
        routes=routes, unserved_order_keys=unserved, deferred_part_keys=sorted(deferred), margin_m=margin,
        vehicles_considered=len(vehicles), onboard_part_keys=sorted(key for key, _ in loaded),
        parts=[OrderPart(part_key=key, order_key=o.order_key, lines=o.lines, weight_kg=o.weight_kg)
               for key, o in loaded + waiting],
    )
