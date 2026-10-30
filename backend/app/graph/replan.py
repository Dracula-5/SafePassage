"""Takes over one route that can no longer finish as planned.

Only goods not yet delivered move to the replacement plan. What is still at the supplier can go on
any vehicle. What is on the truck stays on it: a truck that can still drive delivers it on a new
route; goods on a lost truck (offline or stalled) cannot be replanned and are escalated to humans.
The parent's orders shrink by what moves, so stock is never reserved twice.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import asyncpg

from app.agents.procurement import order_key
from app.agents.schemas import Allocation, OrderDraft, OrderLineDraft, OrderPart

LOST_VEHICLE_KINDS = ("offline", "stalled")
EPSILON = 1e-6


@dataclass
class Seed:
    kind: str
    vehicle_id: str
    allocation: Allocation
    stranded: list[dict]
    onboard_vehicle_id: str | None
    excluded_vehicle_id: str | None


def split_remaining(stops: list[dict], completed: int, parts: list[OrderPart]) -> tuple[list[OrderPart], list[OrderPart]]:
    """(parts still at the supplier, parts on the truck) for a route `completed` stops in."""
    done = stops[:completed]
    picked = {k for s in done if s["kind"] == "pickup" for k in s["order_keys"]}
    dropped = {k for s in done if s["kind"] == "dropoff" for k in s["order_keys"]}
    has_pickup = {k for s in stops if s["kind"] == "pickup" for k in s["order_keys"]}
    left = [p for p in parts if p.part_key not in dropped]
    # A part with no pickup stop was already loaded when this route began (an earlier replan).
    loaded = [p for p in left if p.part_key in picked or p.part_key not in has_pickup]
    waiting = [p for p in left if p.part_key not in {x.part_key for x in loaded}]
    return waiting, loaded


def _totals(parts: list[OrderPart]) -> tuple[dict, float]:
    lines: dict = defaultdict(float)
    for part in parts:
        for line in part.lines:
            lines[line.item_code] += line.quantity
    return lines, sum(p.weight_kg for p in parts)


def redraft(plan_id: str, order: OrderDraft, parts: list[OrderPart], onboard: bool) -> OrderDraft:
    lines, weight = _totals(parts)
    return order.model_copy(update={
        "order_key": order_key(plan_id, order.supplier_id, order.destination_facility_id + (":onboard" if onboard else "")),
        "lines": [OrderLineDraft(item_code=code, quantity=round(qty, 3))
                  for code, qty in sorted(lines.items(), key=lambda kv: kv[0].value)],
        "weight_kg": weight, "onboard": onboard,
    })  # fmt: skip


def _by_order(parts: list[OrderPart]) -> dict[str, list[OrderPart]]:
    grouped: dict[str, list[OrderPart]] = defaultdict(list)
    for part in parts:
        grouped[part.order_key].append(part)
    return grouped


async def _shrink_parent_order(conn: asyncpg.Connection, parent_plan_id: str, order: OrderDraft,
                               parts: list[OrderPart]) -> None:
    order_id = await conn.fetchval(
        "SELECT id FROM orders WHERE plan_id = $1 AND supplier_id = $2 AND destination_facility_id = $3",
        parent_plan_id, order.supplier_id, order.destination_facility_id)  # fmt: skip
    if order_id is None:
        return
    lines, _ = _totals(parts)
    for item, moved in lines.items():
        left = await conn.fetchval("SELECT quantity FROM order_lines WHERE order_id = $1 AND item_code = $2", order_id, item.value)
        if left is None:
            continue
        if left - moved <= EPSILON:
            await conn.execute("DELETE FROM order_lines WHERE order_id = $1 AND item_code = $2", order_id, item.value)
        else:
            await conn.execute("UPDATE order_lines SET quantity = $3 WHERE order_id = $1 AND item_code = $2",
                               order_id, item.value, left - moved)  # fmt: skip
    await conn.execute(
        """UPDATE orders SET status = 'cancelled', updated_at = now()
           WHERE id = $1 AND NOT EXISTS (SELECT 1 FROM order_lines WHERE order_id = $1)""", order_id)


async def take_over_route(pool: asyncpg.Pool, plan_id: str, parent_plan_id: str, route_id: str) -> Seed | None:
    """Abandon the route and return what the replacement plan must carry. None if nothing is left to do."""
    async with pool.acquire() as conn, conn.transaction():
        route = await conn.fetchrow(
            """SELECT vehicle_id::text, status::text, stops, completed_stops, parts FROM routes WHERE id = $1 FOR UPDATE""",
            route_id)  # fmt: skip
        if route is None or route["status"] not in ("planned", "active"):
            return None
        kind = await conn.fetchval(
            """SELECT kind::text FROM route_deviations WHERE route_id = $1 AND resolved_at IS NULL
               ORDER BY detected_at DESC LIMIT 1""", route_id)  # fmt: skip
        allocation = await conn.fetchval("SELECT allocation FROM plans WHERE id = $1", parent_plan_id)
        orders = {o.order_key: o for o in Allocation.model_validate(allocation).orders}
        waiting, loaded = split_remaining(route["stops"], route["completed_stops"],
                                          [OrderPart.model_validate(p) for p in route["parts"]])
        lost = kind in LOST_VEHICLE_KINDS

        drafts = [redraft(plan_id, orders[key], parts, False) for key, parts in _by_order(waiting).items()]
        stranded: list[dict] = []
        if lost:
            for key, parts in _by_order(loaded).items():
                lines, _ = _totals(parts)
                stranded += [{"destination_facility_id": orders[key].destination_facility_id, "item_code": item.value,
                              "quantity_short": qty, "reason": f"on a {kind} vehicle: recover it at its last known position"}
                             for item, qty in lines.items()]  # fmt: skip
        else:
            drafts = [redraft(plan_id, orders[key], parts, True) for key, parts in _by_order(loaded).items()] + drafts

        moved = waiting if lost else [*waiting, *loaded]
        for key, parts in _by_order(moved).items():
            await _shrink_parent_order(conn, parent_plan_id, orders[key], parts)
        await conn.execute("UPDATE routes SET status = 'abandoned', updated_at = now() WHERE id = $1", route_id)
        await conn.execute(
            """UPDATE plans SET status = 'superseded', updated_at = now() WHERE id = $1
               AND NOT EXISTS (SELECT 1 FROM routes WHERE plan_id = $1 AND status <> 'abandoned')""", parent_plan_id)
        await conn.execute("""UPDATE route_deviations SET resolved_at = now(), replan_id = $2
                              WHERE route_id = $1 AND resolved_at IS NULL""", route_id, plan_id)  # fmt: skip
    return Seed(kind=kind or "off_route", vehicle_id=route["vehicle_id"], stranded=stranded,
                allocation=Allocation(orders=drafts, shortfalls=[]),
                onboard_vehicle_id=None if lost or not loaded else route["vehicle_id"],
                excluded_vehicle_id=route["vehicle_id"] if lost else None)  # fmt: skip
