"""Route Planner: hazard-aware Valhalla matrix + OR-Tools pickup/delivery VRP + Valhalla geometry."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.routing.valhalla import Location, LocationNotRoutableError, NoRouteError, ValhallaClient
from app.solvers.geometry import HazardShape, exclusion_rings
from app.solvers.vrp import VrpProblem, VrpStop, VrpVehicle, solve

from .schemas import OrderDraft, OrderPart, PlannedRoute, RouteStop, Routing

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


async def plan_routes(
    valhalla: ValhallaClient,
    orders: list[OrderDraft],
    vehicles: list[VehicleIn],
    sites: dict[str, Site],
    hazards: list[HazardShape],
    start: datetime,
    horizon_s: int,
    attempt: int,
) -> Routing:
    margin = MARGINS_M[min(attempt, len(MARGINS_M)) - 1]
    rings = exclusion_rings(hazards, margin_m=margin, simplify_tolerance_m=margin * 0.4)
    max_cap = max(v.capacity_kg for v in vehicles)
    parts = [p for o in orders for p in split_order(o, max_cap)]

    # Matrix nodes: vehicles first, then each distinct facility used by an order.
    locations: list[Location] = [Location(lon=v.lon, lat=v.lat) for v in vehicles]
    node_of_site: dict[str, int] = {}
    for _, o in parts:
        for fid in (o.supplier_facility_id, o.destination_facility_id):
            if fid not in node_of_site:
                node_of_site[fid] = len(locations)
                locations.append(Location(lon=sites[fid].lon, lat=sites[fid].lat))
    matrix = await valhalla.matrix(locations, locations, exclude=rings)

    stops, pairs = [], []
    for key, o in parts:
        stops.append(VrpStop(stop_id=f"P:{key}", node=node_of_site[o.supplier_facility_id], demand_kg=o.weight_kg,
                             kind="pickup", service_s=SERVICE_S))
        stops.append(VrpStop(stop_id=f"D:{key}", node=node_of_site[o.destination_facility_id], demand_kg=o.weight_kg,
                             kind="dropoff", window_end_s=horizon_s, service_s=SERVICE_S))  # fmt: skip
        pairs.append((f"P:{key}", f"D:{key}"))
    solution = solve(VrpProblem(
        durations_s=matrix.durations_s, stops=stops, pairs=pairs, horizon_s=horizon_s, time_limit_s=5,
        vehicles=[VrpVehicle(vehicle_id=v.vehicle_id, start_node=i, end_node=i, capacity_kg=v.capacity_kg)
                  for i, v in enumerate(vehicles)],
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
            geo = await valhalla.route(waypoints, exclude=rings) if len(waypoints) > 1 else None
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
        routes=routes, unserved_order_keys=unserved, margin_m=margin, vehicles_considered=len(vehicles),
        parts=[OrderPart(part_key=key, order_key=o.order_key, lines=o.lines, weight_kg=o.weight_kg) for key, o in parts],
    )
