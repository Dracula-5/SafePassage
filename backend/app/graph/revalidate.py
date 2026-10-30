"""Re-checks an approved plan against the world as it is now, just before dispatch.

Approval can come hours after planning: the hazard may have ended or spread, another plan may
have taken the stock, a driver may have gone offline. Plans are never dispatched on stale facts.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.agents import data
from app.agents.critic import review
from app.agents.schemas import Allocation, OrderDraft, OrderLineDraft, Routing
from app.routing.repository import fetch_active_hazards

MIN_DELAY = timedelta(minutes=1)  # shorter delays do not change anything a driver would notice


def carried_orders(allocation: Allocation, routing: Routing) -> list[OrderDraft]:
    """Orders cut down to what a vehicle actually carries.

    A split order can have parts deferred to the next wave or left unserved; ordering its full
    quantity would reserve stock and promise goods that nothing will move.
    """
    routed = {key for route in routing.routes for key in route.order_keys}
    quantities: dict[str, dict] = {}
    weights: dict[str, float] = {}
    for part in routing.parts:
        if part.part_key not in routed:
            continue
        lines = quantities.setdefault(part.order_key, {})
        for line in part.lines:
            lines[line.item_code] = lines.get(line.item_code, 0.0) + line.quantity
        weights[part.order_key] = weights.get(part.order_key, 0.0) + part.weight_kg
    return [
        order.model_copy(update={
            "lines": [OrderLineDraft(item_code=code, quantity=round(qty, 3))
                      for code, qty in sorted(quantities[order.order_key].items(), key=lambda kv: kv[0].value)],
            "weight_kg": weights[order.order_key],
        })
        for order in allocation.orders if order.order_key in quantities
    ]  # fmt: skip


def shift_etas(routing: Routing, delay: timedelta) -> Routing:
    """Everything happens later than planned by the time approval took."""
    return routing.model_copy(update={"routes": [
        route.model_copy(update={"stops": [s.model_copy(update={"eta": s.eta + delay}) for s in route.stops]})
        for route in routing.routes
    ]})  # fmt: skip


async def stock_problems(conn, orders: list[OrderDraft]) -> list[str]:
    rows = await data.available_stock(conn, sorted({line.item_code.value for o in orders for line in o.lines}))
    available = {(r["supplier_id"], r["item_code"]): r["available"] for r in rows}
    problems = []
    for order in orders:
        for line in order.lines:
            have = available.get((order.supplier_id, line.item_code.value), 0.0)
            if have + 1e-6 < line.quantity:
                problems.append(f"{order.supplier_name} now has {have:.0f} {line.item_code.value}, "
                                f"plan needs {line.quantity:.0f}")
    return problems


async def recheck(deps, state, now: datetime) -> tuple[list[str], Routing]:
    """Returns (problems, routing with up-to-date ETAs). No problems means safe to dispatch."""
    routing = Routing.model_validate(state["routing"])
    allocation = Allocation.model_validate(state["allocation"])
    started = datetime.fromisoformat(state["started_at"])
    delay = now - started
    if delay >= MIN_DELAY:
        routing = shift_etas(routing, delay)
    problems: list[str] = []

    replan = bool(state.get("replan_route_id"))  # goods already on the road: only the road itself is re-checked
    if not replan and not await data.hazard_facts(deps.pool, state["hazard_ids"]):
        problems.append("the hazard this plan responds to is no longer active")
    points = [pt for route in routing.routes for pt in route.geometry]
    margin = 0.5
    lons, lats = [p[0] for p in points], [p[1] for p in points]
    hazards = await fetch_active_hazards(deps.pool, bbox=(min(lons) - margin, min(lats) - margin,
                                                          max(lons) + margin, max(lats) + margin))  # fmt: skip
    horizon_s = state["triage"]["planning_horizon_hours"] * 3600
    verdict = review(routing, hazards, state.get("attempt", 1), now, horizon_s, deps.settings.min_route_clearance_m)
    problems += verdict.violations
    if not replan:
        problems += await stock_problems(deps.pool, carried_orders(allocation, routing))
    usable = {r["id"] for r in await data.usable_vehicles(deps.pool)}
    problems += [f"{r.vehicle_label} is offline or already assigned" for r in routing.routes if r.vehicle_id not in usable]
    return problems, routing
