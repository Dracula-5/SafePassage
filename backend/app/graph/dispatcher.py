"""Dispatcher: persist orders and routes idempotently, then driver instructions, PO PDFs, messages.

Idempotency: every order has a key derived from (plan, supplier, destination, lines); routes are
written once per plan; every message has a key derived from (plan, route|order, channel). A
dispatcher that runs again (worker restart, duplicate resume) changes nothing.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from reportlab.platypus.doctemplate import LayoutError
from shapely import LineString

from app.agents import reasoning
from app.agents.schemas import Allocation, Demand, PlannedRoute, Routing
from app.core.resilience import SourceUnavailable
from app.dispatch.documents import purchase_order_pdf
from app.dispatch.messaging import SendResult
from app.llm.client import LLMOutputError

from . import plans
from .audit import Audit
from .revalidate import carried_orders

if TYPE_CHECKING:
    from .nodes import Deps, PlanState


DRIVER_TZ = ZoneInfo("Asia/Kolkata")  # drivers work in local time; a UTC deadline would be 5.5 hours out
DRILL_NOT_SENT = "not sent: drills do not message real people"


def idempotency_key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def english_instructions(route: PlannedRoute, part_lines: dict[str, list], drill: bool, updated: bool = False) -> str:
    lines = [("DRILL - NOT A REAL DISPATCH. " if drill else "") + ("NEW ROUTE, replaces your earlier one. " if updated else "")
             + f"Vehicle {route.vehicle_label}."]  # fmt: skip
    for i, stop in enumerate(route.stops, 1):
        goods = defaultdict(float)
        for key in stop.order_keys:
            for line in part_lines[key]:
                goods[line.item_code.value] += line.quantity
        what = ", ".join(f"{qty:.0f} {code}" for code, qty in sorted(goods.items()))
        verb = "Pick up" if stop.kind == "pickup" else "Deliver"
        lines.append(f"{i}. {verb} at {stop.name} by {stop.eta.astimezone(DRIVER_TZ):%H:%M} IST: {what}.")
    lines.append(f"Total {route.distance_m / 1000:.0f} km. The route avoids active hazard zones; do not take shortcuts.")
    return "\n".join(lines)


def held_back_escalations(routing: Routing, allocation: Allocation) -> list[dict]:
    """Goods no vehicle takes in this plan, per destination and item, in the Shortfall shape."""
    destination = {o.order_key: o.destination_facility_id for o in allocation.orders}
    parts = {p.part_key: p for p in routing.parts}
    reasons = (("next wave: beyond what the available vehicles can carry in this one", routing.deferred_part_keys),
               ("not reachable by any vehicle within a shift and the planning horizon", routing.unserved_order_keys))  # fmt: skip
    out = []
    for reason, keys in reasons:
        held: dict[tuple[str, str], float] = defaultdict(float)
        for key in keys:
            for line in parts[key].lines:
                held[(destination[parts[key].order_key], line.item_code.value)] += line.quantity
        out += [{"destination_facility_id": dest, "item_code": item, "quantity_short": qty, "reason": reason}
                for (dest, item), qty in sorted(held.items())]  # fmt: skip
    return out


async def dispatch(deps: "Deps", audit: Audit, state: "PlanState") -> str:
    plan_id, drill = state["plan_id"], state.get("is_drill", False)
    allocation = Allocation.model_validate(state["allocation"])
    routing = Routing.model_validate(state["routing"])
    demand = Demand.model_validate(state["demand"])
    names = {p.facility_id: p.name for p in demand.relief_points}
    part_lines = {p.part_key: p.lines for p in routing.parts}
    orders = carried_orders(allocation, routing)  # never dispatch goods no vehicle will carry
    now = datetime.now(UTC)
    await audit("dispatcher", "node_started", f"Dispatching {len(routing.routes)} route(s), {len(orders)} order(s)")

    order_ids: dict[str, str] = {}
    route_ids: dict[str, str] = {}
    async with deps.pool.acquire() as conn, conn.transaction():
        existing = {r["vehicle_id"]: str(r["id"]) for r in await conn.fetch(
            "SELECT id, vehicle_id::text FROM routes WHERE plan_id = $1", plan_id)}
        for route in routing.routes:
            if route.vehicle_id in existing:
                route_ids[route.vehicle_id] = existing[route.vehicle_id]
                continue
            route_ids[route.vehicle_id] = str(await conn.fetchval(
                """INSERT INTO routes (plan_id, vehicle_id, status, geom, distance_m, duration_s, stops, load_kg,
                                       min_hazard_clearance_m, parts)
                   VALUES ($1, $2, 'planned', ST_SetSRID(ST_GeomFromText($3), 4326), $4, $5, $6, $7, $8, $9) RETURNING id""",
                plan_id, route.vehicle_id, LineString(route.geometry).wkt, route.distance_m, route.duration_s,
                json.loads(json.dumps([s.model_dump(mode="json") for s in route.stops])), route.load_kg,
                ((state.get("verdicts") or [{}])[-1].get("min_clearance_m") or {}).get(route.vehicle_id),
                [p.model_dump(mode="json") for p in routing.parts if p.part_key in route.order_keys],
            ))  # fmt: skip
        for order in orders:
            key = idempotency_key(plan_id, order.supplier_id, order.destination_facility_id,
                                  json.dumps([l.model_dump(mode="json") for l in order.lines], sort_keys=True))
            carrier = next(r.vehicle_id for r in routing.routes
                           if any(k.split("#")[0] == order.order_key for k in r.order_keys))
            row = await conn.fetchrow(
                """INSERT INTO orders (plan_id, route_id, supplier_id, destination_facility_id, status, idempotency_key,
                                       dispatched_at)
                   VALUES ($1, $2, $3, $4, $6, $5, now())
                   ON CONFLICT (idempotency_key) DO NOTHING RETURNING id""",
                plan_id, route_ids[carrier], order.supplier_id, order.destination_facility_id, key,
                "in_transit" if order.onboard else "dispatched",
            )  # fmt: skip
            order_id = str(row["id"]) if row else str(await conn.fetchval("SELECT id FROM orders WHERE idempotency_key = $1", key))
            order_ids[order.order_key] = order_id
            for line in order.lines:
                await conn.execute(
                    """INSERT INTO order_lines (order_id, item_code, quantity) VALUES ($1, $2, $3)
                       ON CONFLICT DO NOTHING""", order_id, line.item_code.value, line.quantity)
        await conn.execute("UPDATE plans SET status = 'dispatched', updated_at = now() WHERE id = $1", plan_id)
    await audit("dispatcher", "dispatch", f"{len(order_ids)} order(s) and {len(route_ids)} route(s) committed",
                {"orders": order_ids, "routes": route_ids},
                [f"order:{o}" for o in order_ids.values()] + [f"route:{r}" for r in route_ids.values()])

    # Driver instructions in the driver's language (translation guarded: numbers must not change).
    for route in routing.routes:
        english = english_instructions(route, part_lines, drill, updated=bool(state.get("replan_route_id")))
        text, language = english, "en"
        if route.driver_language != "en":
            if deps.llm is None:
                await audit("dispatcher", "data_stale", f"No LLM; {route.vehicle_label} gets English instructions")
            else:
                try:
                    result = await reasoning.translate_instructions(deps.llm, english, route.driver_language)
                    text, language = result.value.text, route.driver_language
                    await audit("dispatcher", "tool_call", f"Translated instructions for {route.vehicle_label} into "
                                f"{reasoning.LANGUAGE_NAMES.get(language, language)} (numbers verified)", result.audit())
                except (LLMOutputError, SourceUnavailable) as exc:
                    await audit("dispatcher", "error", f"Translation failed for {route.vehicle_label}; sending English",
                                {"reason": str(exc)})
        route_id = route_ids[route.vehicle_id]
        await deps.pool.execute("UPDATE routes SET instructions = $2, updated_at = now() WHERE id = $1", route_id,
                                {"language": language, "text": text, "source_text": english})
        phone = await deps.pool.fetchval("SELECT driver_phone FROM v_vehicle_status WHERE id = $1::uuid", route.vehicle_id)
        await _notify(deps, audit, route_id=route_id, order_id=None, channel="sms", to=phone, language=language, body=text,
                      key=idempotency_key(plan_id, route_id, "sms"), who=f"driver of {route.vehicle_label}", drill=drill)

    # Purchase orders: PDF + supplier message.
    for order in orders:
        if order.onboard:
            continue  # the supplier handed these over already
        order_id = order_ids[order.order_key]
        key = await deps.pool.fetchval("SELECT idempotency_key FROM orders WHERE id = $1::uuid", order_id)
        try:
            path = purchase_order_pdf(deps.settings.documents_dir / plan_id / f"PO-{order.order_key}.pdf", plan_id, order,
                                      names.get(order.destination_facility_id, order.destination_facility_id), key, drill, now)
            await audit("dispatcher", "tool_result", f"Purchase order PDF for {order.supplier_name}", {"path": str(path)},
                        [f"order:{order_id}", f"supplier:{order.supplier_id}"])
        except (OSError, ValueError, LayoutError) as exc:
            # The trucks already have their routes, so a full disk must not mark a dispatched plan as failed.
            await audit("dispatcher", "error", f"Purchase order PDF for {order.supplier_name} was not written; the order "
                        "stands and the supplier message still goes", {"reason": f"{type(exc).__name__}: {exc}"[:300]},
                        [f"order:{order_id}", f"supplier:{order.supplier_id}"])
        supplier = await deps.pool.fetchrow(
            "SELECT contact_whatsapp, contact_phone, preferred_language FROM v_suppliers WHERE id = $1::uuid", order.supplier_id)
        body = (("DRILL. " if drill else "") + f"ReliefRoute PO {order.order_key}: prepare "
                + ", ".join(f"{l.quantity:.0f} {l.item_code.value}" for l in order.lines)
                + f" for pickup; deliver to {names.get(order.destination_facility_id, 'relief point')}.")  # fmt: skip
        channel = "whatsapp" if supplier and supplier["contact_whatsapp"] else "sms"
        to = supplier and (supplier["contact_whatsapp"] or supplier["contact_phone"])
        await _notify(deps, audit, route_id=None, order_id=order_id, channel=channel, to=to, language="en", body=body,
                      key=idempotency_key(plan_id, order_id, channel), who=f"supplier {order.supplier_name}", drill=drill)
    await plans.set_status(deps.pool, plan_id, "dispatched")
    return f"{len(order_ids)} orders on {len(route_ids)} routes"


async def _notify(deps: "Deps", audit: Audit, *, route_id: str | None, order_id: str | None, channel: str,
                  to: str | None, language: str, body: str, key: str, who: str, drill: bool) -> None:
    if not to:
        await audit("dispatcher", "error", f"No phone number for {who}; message not sent", {"idempotency_key": key})
        return
    inserted = await deps.pool.fetchval(
        """INSERT INTO notifications (order_id, route_id, channel, recipient, language, body, idempotency_key)
           VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (idempotency_key) DO NOTHING RETURNING id""",
        order_id, route_id, channel, to, language, body, key,
    )  # fmt: skip
    if inserted is None:
        await audit("dispatcher", "decision", f"Message to {who} already handled (idempotent)", {"idempotency_key": key})
        return
    silent = drill and not deps.settings.drill_send_messages
    result = SendResult("failed", None, DRILL_NOT_SENT) if silent else await deps.messenger.send(channel, to, body)
    await deps.pool.execute("UPDATE notifications SET status = $2, provider_ref = $3, error = $4, updated_at = now() "
                            "WHERE id = $1", inserted, result.status, result.provider_ref, result.error)
    await audit("dispatcher", "decision" if silent else "dispatch" if result.status == "sent" else "error",
                f"{channel.upper()} to {who}: {result.status}" + (f" ({result.error})" if result.error else ""),
                {"notification_id": str(inserted), "idempotency_key": key})
