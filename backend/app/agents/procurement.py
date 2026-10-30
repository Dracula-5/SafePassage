"""Procurement: split demand across suppliers using live available stock.

Greedy by road travel time from supplier to destination (hazard-aware matrix). Verified
suppliers are preferred at equal reach. Quantities never exceed `available` in
v_available_stock. Anything left unmet becomes a Shortfall for escalation.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass

from .schemas import Allocation, DemandLine, OrderDraft, OrderLineDraft, Shortfall


@dataclass
class StockRow:
    supplier_id: str
    supplier_name: str
    verified: bool
    facility_id: str
    lon: float
    lat: float
    item_code: str
    available: float
    unit_weight_kg: float


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    import math

    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


def nearby_candidates(stock: list[StockRow], points: list[tuple[float, float]], radius_m: float,
                      per_item: int) -> list[StockRow]:
    """Suppliers within radius_m (straight line) of any relief point, nearest `per_item` per item.

    Keeps the road-time matrix inside the router's limits; farther stock is not considered.
    """
    def nearest(s: StockRow) -> float:
        return min(haversine_m(s.lon, s.lat, lon, lat) for lon, lat in points)

    by_item: dict[str, list[tuple[float, StockRow]]] = defaultdict(list)
    for s in stock:
        d = nearest(s)
        if d <= radius_m:
            by_item[s.item_code].append((d, s))
    return [s for rows in by_item.values() for _, s in sorted(rows, key=lambda x: x[0])[:per_item]]


def order_key(plan_id: str, supplier_id: str, destination_id: str) -> str:
    return hashlib.sha256(f"{plan_id}|{supplier_id}|{destination_id}".encode()).hexdigest()[:16]


def allocate(
    plan_id: str,
    demand: list[DemandLine],
    stock: list[StockRow],
    travel_s: dict[tuple[str, str], float | None],
) -> Allocation:
    """travel_s[(supplier_id, destination_facility_id)] = seconds, or None if no safe road."""
    remaining = {(s.supplier_id, s.item_code): s.available for s in stock}
    by_item: dict[str, list[StockRow]] = defaultdict(list)
    for s in stock:
        by_item[s.item_code].append(s)
    picks: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    shortfalls: list[Shortfall] = []

    for line in demand:
        need = line.quantity
        dest = line.destination_facility_id
        candidates = [s for s in by_item.get(line.item_code.value, [])
                      if travel_s.get((s.supplier_id, dest)) is not None]  # fmt: skip
        candidates.sort(key=lambda s: (travel_s[(s.supplier_id, dest)], not s.verified))
        for s in candidates:
            if need <= 0:
                break
            take = min(need, remaining[(s.supplier_id, s.item_code)])
            if take <= 0:
                continue
            remaining[(s.supplier_id, s.item_code)] -= take
            picks[(s.supplier_id, dest)][s.item_code] += take
            need -= take
        if need > 0:
            unreachable = any(travel_s.get((s.supplier_id, dest)) is None for s in by_item.get(line.item_code.value, []))
            reason = ("no supplier holds this item" if not by_item.get(line.item_code.value)
                      else "suppliers with stock cannot reach this destination safely" if not candidates and unreachable
                      else "insufficient stock across reachable suppliers")  # fmt: skip
            shortfalls.append(Shortfall(destination_facility_id=dest, item_code=line.item_code,
                                        quantity_short=need, reason=reason))  # fmt: skip

    rows = {(s.supplier_id, s.item_code): s for s in stock}
    orders = []
    for (supplier_id, dest), items in picks.items():
        any_row = next(rows[(supplier_id, code)] for code in items)
        orders.append(OrderDraft(
            order_key=order_key(plan_id, supplier_id, dest), supplier_id=supplier_id,
            supplier_name=any_row.supplier_name, supplier_verified=any_row.verified,
            supplier_facility_id=any_row.facility_id, supplier_lon=any_row.lon, supplier_lat=any_row.lat,
            destination_facility_id=dest,
            lines=[OrderLineDraft(item_code=code, quantity=qty) for code, qty in sorted(items.items())],
            weight_kg=sum(qty * rows[(supplier_id, code)].unit_weight_kg for code, qty in items.items()),
            travel_s=travel_s[(supplier_id, dest)],
        ))  # fmt: skip
    return Allocation(orders=sorted(orders, key=lambda o: o.order_key), shortfalls=shortfalls)
