"""Graph nodes. Each validates its inputs from state, records tool calls and decisions in
agent_events (with source ids), and returns validated outputs as JSON-able dicts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypedDict

import asyncpg
from langgraph.types import interrupt

from app.agents import data, reasoning
from app.agents.critic import review
from app.agents.needs import compute_demand
from app.agents.planner import Site, VehicleIn, plan_routes
from app.agents.procurement import StockRow, allocate, haversine_m, nearby_candidates
from app.agents.reasoning import PlanBrief
from app.agents.schemas import Allocation, Demand, HazardFact, Routing, Triage
from app.core.resilience import SourceUnavailable
from app.core.settings_brain import BrainSettings
from app.dispatch.messaging import Messenger
from app.llm.client import LLMClient, LLMOutputError
from app.routing.repository import fetch_active_hazards
from app.routing.valhalla import Location, ValhallaClient, ValhallaError
from app.solvers.geometry import exclusion_rings

from . import fallbacks, plans
from .audit import Audit
from .dispatcher import deferred_escalations, dispatch
from .replan import take_over_route
from .revalidate import recheck


class PlanState(TypedDict, total=False):
    plan_id: str
    thread_id: str
    hazard_ids: list[str]
    parent_plan_id: str
    replan_route_id: str        # set when this plan replaces one route of its parent
    onboard_vehicle_id: str
    excluded_vehicle_id: str
    is_drill: bool
    started_at: str
    hazards: list[dict]
    triage: dict
    demand: dict
    allocation: dict
    routing: dict
    verdicts: list[dict]
    attempt: int
    requires_approval: bool
    approval_reasons: list[str]
    brief: dict
    decision: dict
    outcome: str          # terminal plan status when set
    outcome_reason: str


@dataclass
class Deps:
    pool: asyncpg.Pool
    valhalla: ValhallaClient
    llm: LLMClient | None
    messenger: Messenger
    settings: BrainSettings


def _ids(prefix: str, ids: list[str]) -> list[str]:
    return [f"{prefix}:{i}" for i in ids]


STOPPED_WITH_REASON = ("unreachable", "failed", "superseded")
ALTERNATE_HUB_RADIUS_FACTOR = 4
ALTERNATE_HUB_CANDIDATES = 40
AREA_MARGIN_DEG = 0.5  # about 50 km: hazards farther than this from every location cannot affect the trip


def _area(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    """Bounding box of the trip's locations plus a margin; only hazards inside matter for routing."""
    lons, lats = [p[0] for p in points], [p[1] for p in points]
    m = AREA_MARGIN_DEG
    return min(lons) - m, min(lats) - m, max(lons) + m, max(lats) + m


def _stop(state: PlanState, status: str, reason: str) -> dict:
    return {"outcome": status, "outcome_reason": reason}


async def _alternate_hub(deps: Deps, state: PlanState, demand: Demand, vehicles: list[VehicleIn],
                         allocation: Allocation, hazards: list) -> dict | None:
    """The nearest safe relief point beyond the normal search that a vehicle and a supplier can both
    reach. Only a proposal: a human decides whether to move the relief effort there."""
    s = deps.settings
    known = {p.facility_id for p in demand.relief_points}
    wider = await data.relief_points(deps.pool, state["hazard_ids"], s.relief_search_radius_m * ALTERNATE_HUB_RADIUS_FACTOR,
                                     ALTERNATE_HUB_CANDIDATES)  # fmt: skip
    candidates = [p for p in wider if p.facility_id not in known]
    if not candidates:
        return None
    suppliers = sorted({(o.supplier_lon, o.supplier_lat) for o in allocation.orders})
    origins = [Location(lon=v.lon, lat=v.lat) for v in vehicles] + [Location(lon=lo, lat=la) for lo, la in suppliers]
    targets = [Location(lon=p.lon, lat=p.lat) for p in candidates]
    try:
        matrix = await deps.valhalla.matrix(origins, targets, exclude=exclusion_rings(hazards))
    except (ValhallaError, SourceUnavailable):
        return None
    for j, point in enumerate(candidates):
        from_vehicle = any(matrix.durations_s[i][j] is not None for i in range(len(vehicles)))
        from_supplier = not suppliers or any(matrix.durations_s[i][j] is not None for i in range(len(vehicles), len(origins)))
        if from_vehicle and from_supplier:
            return {"kind": "alternate_hub", "facility_id": point.facility_id, "name": point.name, "lon": point.lon,
                    "lat": point.lat, "reason": f"alternate hub: {point.name} ({point.kind}) is reachable by a safe road"}
    return None


async def _llm_or_fallback(audit: Audit, node: str, what: str, call, fallback):
    """Run an LLM step; on missing key/outage/invalid output use the labelled rule-based fallback."""
    deps_llm_missing = call is None
    if not deps_llm_missing:
        try:
            result = await call()
            await audit(node, "tool_call", f"LLM {what}", result.audit())
            return result.value
        except (LLMOutputError, SourceUnavailable) as exc:
            reason = f"{type(exc).__name__}: {exc}"
            attempts = [a.error for a in getattr(exc, "attempts", [])]
            await audit(node, "error", f"LLM {what} failed; using rule-based fallback", {"reason": reason, "errors": attempts})
            return fallback(reason[:200])
    await audit(node, "data_stale", f"LLM not configured; rule-based {what}", {"reason": "no LLM key"})
    return fallback("LLM not configured")


def make_nodes(deps: Deps) -> dict[str, Any]:
    s = deps.settings

    def audit_for(state: PlanState) -> Audit:
        return Audit(deps.pool, state["plan_id"], state["thread_id"])

    async def replan_seed(state: PlanState) -> dict:
        audit = audit_for(state)
        parent_id, route_id = state["parent_plan_id"], state["replan_route_id"]
        await audit("replanner", "node_started", f"Replanning what is left of route {route_id}", {}, [f"route:{route_id}"])
        seed = await take_over_route(deps.pool, state["plan_id"], parent_id, route_id)
        if seed is None:
            return _stop(state, "superseded", "the route had already finished or been replaced")
        parent = await deps.pool.fetchrow(
            "SELECT demand, triage, is_drill, hazard_ids::text[] FROM plans WHERE id = $1", parent_id)
        hazards = await data.hazard_facts(deps.pool, parent["hazard_ids"])
        allocation = seed.allocation
        await plans.set_status(deps.pool, state["plan_id"], "planning", allocation=allocation.model_dump(mode="json"),
                               demand=parent["demand"], triage=parent["triage"], escalations=seed.stranded,
                               is_drill=parent["is_drill"])  # fmt: skip
        await audit("replanner", "decision",
                    f"{seed.kind.replace('_', ' ')}: {len(allocation.orders)} order(s) to re-route, "
                    f"{len(seed.stranded)} line(s) stranded on the vehicle", allocation.model_dump(mode="json"),
                    [f"route:{route_id}", f"vehicle:{seed.vehicle_id}"])  # fmt: skip
        if not allocation.orders:
            return _stop(state, "failed", "nothing can be re-routed: the goods are on a vehicle that cannot move")
        return {"allocation": allocation.model_dump(mode="json"), "demand": parent["demand"], "triage": parent["triage"],
                "hazards": [h.model_dump(mode="json") for h in hazards], "is_drill": parent["is_drill"], "attempt": 1,
                "verdicts": [], "onboard_vehicle_id": seed.onboard_vehicle_id,
                "excluded_vehicle_id": seed.excluded_vehicle_id}  # fmt: skip

    async def sentinel(state: PlanState) -> dict:
        audit = audit_for(state)
        await audit("sentinel", "node_started", f"Triage of {len(state['hazard_ids'])} hazard(s)")
        hazards = await data.hazard_facts(deps.pool, state["hazard_ids"])
        await audit("sentinel", "tool_result", f"Read {len(hazards)} active hazard(s) from v_active_hazards",
                    {"hazards": [h.model_dump(mode="json") for h in hazards]}, _ids("hazard", [h.hazard_id for h in hazards]))
        if not hazards:
            return _stop(state, "failed", "none of the requested hazards is still active")
        population = await data.population_in(deps.pool, state["hazard_ids"])
        points = await data.relief_points(deps.pool, state["hazard_ids"], s.relief_search_radius_m, s.relief_points_per_plan)
        await audit("sentinel", "tool_result",
                    f"Census population {population.population_lower_bound:,} from "
                    f"{population.places_with_population}/{population.places_total} settlements in the zone that have a figure "
                    f"(untagged settlements add none; {population.places_scaled} city/town figure(s) scaled by the zone's "
                    f"share of their mandal, {population.population_unscaled:,} before scaling); "
                    f"{len(points)} safe relief points within {s.relief_search_radius_m / 1000:.0f} km",
                    population.model_dump(), ["view:v_places", "view:v_facilities"])
        triage: Triage = await _llm_or_fallback(
            audit, "sentinel", "triage",
            (lambda: reasoning.triage(deps.llm, hazards, population, len(points))) if deps.llm else None,
            lambda reason: fallbacks.triage(hazards, population, reason))
        await audit("sentinel", "decision", f"Priority {triage.priority}, plan {triage.planning_horizon_hours} h: "
                    f"{triage.rationale}", triage.model_dump(), _ids("hazard", [h.hazard_id for h in hazards]))
        await plans.set_status(deps.pool, state["plan_id"], "planning", triage=triage.model_dump(),
                               is_drill=any(h.is_drill for h in hazards))
        return {"hazards": [h.model_dump(mode="json") for h in hazards], "triage": triage.model_dump(), "attempt": 1,
                "verdicts": [], "is_drill": any(h.is_drill for h in hazards)}

    async def needs(state: PlanState) -> dict:
        audit = audit_for(state)
        triage = Triage.model_validate(state["triage"])
        population = await data.population_in(deps.pool, state["hazard_ids"])
        points = await data.relief_points(deps.pool, state["hazard_ids"], s.relief_search_radius_m, s.relief_points_per_plan)
        horizon_days = min(triage.planning_horizon_hours / 24, s.planning_horizon_days_max)
        demand = compute_demand(population, points, horizon_days)
        await audit("needs", "decision",
                    f"{len(demand.lines)} demand lines for {len(points)} relief points over {horizon_days:g} day(s); "
                    f"population from census-tagged settlements only ({population.coverage:.0%} settlement coverage)",
                    demand.model_dump(mode="json"), _ids("facility", [p.facility_id for p in points]) + ["view:v_places"])
        await plans.set_status(deps.pool, state["plan_id"], "planning", demand=demand.model_dump(mode="json"))
        if not points:
            return _stop(state, "unreachable", "no safe relief point (shelter/school/hospital outside every hazard) nearby")
        if not demand.lines:
            return _stop(state, "failed", "no census population recorded inside the hazard zone; demand cannot be "
                                          "computed without guessing")
        return {"demand": demand.model_dump(mode="json")}

    async def procurement(state: PlanState) -> dict:
        audit = audit_for(state)
        demand = Demand.model_validate(state["demand"])
        items = sorted({line.item_code.value for line in demand.lines})
        rows = await data.available_stock(deps.pool, items)
        stock = [StockRow(supplier_id=r["supplier_id"], supplier_name=r["supplier_name"], verified=r["verified"],
                          facility_id=r["facility_id"], lon=r["lon"], lat=r["lat"], item_code=r["item_code"],
                          available=r["available"], unit_weight_kg=r["unit_weight_kg"]) for r in rows]  # fmt: skip
        await audit("procurement", "tool_result", f"{len(stock)} stock rows with availability from v_available_stock",
                    {"stock": [dict(r) for r in rows]}, _ids("stock", [r["stock_id"] for r in rows]))
        points = [(p.lon, p.lat) for p in demand.relief_points]
        nearby = nearby_candidates(stock, points, s.procurement_radius_m, s.procurement_max_suppliers_per_item)
        if len(nearby) < len(stock):
            await audit("procurement", "decision", f"Considering {len(nearby)} of {len(stock)} stock rows: nearest "
                        f"{s.procurement_max_suppliers_per_item} per item within {s.procurement_radius_m / 1000:.0f} km")
        stock = nearby
        travel: dict[tuple[str, str], float | None] = {}
        if stock:
            suppliers = sorted({(x.supplier_id, x.lon, x.lat) for x in stock})
            hazards = await fetch_active_hazards(deps.pool, bbox=_area([(lo, la) for _, lo, la in suppliers] + points))
            rings = exclusion_rings(hazards)
            targets = [Location(lon=p.lon, lat=p.lat) for p in demand.relief_points]
            chunk = max(1, 2500 // max(1, len(targets)) // 2)  # stay well under max_matrix_location_pairs
            for start_i in range(0, len(suppliers), chunk):
                part = suppliers[start_i : start_i + chunk]
                try:
                    matrix = await deps.valhalla.matrix([Location(lon=lo, lat=la) for _, lo, la in part], targets, exclude=rings)
                    rows = matrix.durations_s
                except (ValhallaError, SourceUnavailable) as exc:
                    await audit("procurement", "error", f"Valhalla matrix chunk failed ({exc}); those suppliers count as unreachable")
                    rows = [[None] * len(targets) for _ in part]
                for i, (sid, _, _) in enumerate(part):
                    for j, p in enumerate(demand.relief_points):
                        travel[(sid, p.facility_id)] = rows[i][j]
            await audit("procurement", "tool_call", f"Valhalla matrix {len(suppliers)}x{len(targets)} around "
                        f"{len(hazards)} active hazard(s)", {"reachable_pairs": sum(v is not None for v in travel.values())},
                        _ids("hazard", [h.hazard_id for h in hazards]))
        allocation = allocate(state["plan_id"], demand.lines, stock, travel)
        escalations = [x.model_dump(mode="json") for x in allocation.shortfalls]
        await audit("procurement", "decision",
                    f"{len(allocation.orders)} orders across {len({o.supplier_id for o in allocation.orders})} supplier(s); "
                    f"{len(allocation.shortfalls)} shortfall(s) escalated", allocation.model_dump(mode="json"),
                    _ids("supplier", sorted({o.supplier_id for o in allocation.orders})))
        await plans.set_status(deps.pool, state["plan_id"], "planning", allocation=allocation.model_dump(mode="json"),
                               escalations=escalations)
        if not allocation.orders:
            return _stop(state, "failed", "no supplier has reachable stock for this demand: escalated to humans")
        return {"allocation": allocation.model_dump(mode="json")}

    async def route_planner(state: PlanState) -> dict:
        audit = audit_for(state)
        allocation = Allocation.model_validate(state["allocation"])
        demand = Demand.model_validate(state["demand"])
        rows = [r for r in await data.usable_vehicles(deps.pool) if r["id"] != state.get("excluded_vehicle_id")]
        offline = await data.offline_vehicle_count(deps.pool)
        await audit("route_planner", "tool_result", f"{len(rows)} online vehicle(s) with a GPS fix; {offline} offline",
                    {"vehicles": [dict(r) for r in rows]}, _ids("vehicle", [r["id"] for r in rows]))
        if not rows:
            return _stop(state, "failed", f"no online vehicle with a GPS fix ({offline} offline): escalated to humans")
        centre = demand.relief_points[0]
        rows = [r for r in rows if haversine_m(r["lon"], r["lat"], centre.lon, centre.lat) <= s.vehicle_radius_m]
        if not rows:
            return _stop(state, "failed", f"no online vehicle within {s.vehicle_radius_m / 1000:.0f} km: escalated to humans")
        vehicles = [VehicleIn(vehicle_id=r["id"], label=r["label"], capacity_kg=r["capacity_kg"],
                              driver_language=r["driver_language"], lon=r["lon"], lat=r["lat"]) for r in rows]  # fmt: skip
        onboard_vehicle = state.get("onboard_vehicle_id")
        if onboard_vehicle and all(v.vehicle_id != onboard_vehicle for v in vehicles):
            return _stop(state, "failed", "the vehicle carrying these goods went offline during replanning: escalated to humans")
        sites = {p.facility_id: Site(p.facility_id, p.name, p.lon, p.lat) for p in demand.relief_points}
        for o in allocation.orders:
            sites[o.supplier_facility_id] = Site(o.supplier_facility_id, o.supplier_name, o.supplier_lon, o.supplier_lat)
        trip = [(v.lon, v.lat) for v in vehicles] + [(x.lon, x.lat) for x in sites.values()]
        hazards = await fetch_active_hazards(deps.pool, bbox=_area(trip))
        triage = Triage.model_validate(state["triage"])
        try:
            routing = await plan_routes(deps.valhalla, allocation.orders, vehicles, sites, hazards,
                                        datetime.fromisoformat(state["started_at"]), triage.planning_horizon_hours * 3600,
                                        state["attempt"], max_trips=s.max_trips_per_vehicle,
                                        shift_s=int(s.max_shift_hours * 3600), onboard_vehicle_id=onboard_vehicle)  # fmt: skip
        except (ValhallaError, SourceUnavailable) as exc:
            await audit("route_planner", "error", f"Routing service unavailable: {exc}")
            return _stop(state, "failed", "routing service unavailable, so no route can be verified as safe: escalated to humans")
        await audit("route_planner", "decision",
                    f"Attempt {state['attempt']}: {len(routing.routes)} route(s), {len(routing.unserved_order_keys)} "
                    f"unserved order part(s), {len(routing.deferred_part_keys)} part(s) deferred to the next wave, "
                    f"exclusion margin {routing.margin_m:.0f} m around {len(hazards)} hazard(s)",
                    routing.model_dump(mode="json", exclude={"routes": {"__all__": {"geometry"}}}),
                    _ids("hazard", [h.hazard_id for h in hazards]) + _ids("vehicle", [r.vehicle_id for r in routing.routes]))
        if not routing.routes:
            reason = "no safe road route for any order: every path crosses an active hazard or no vehicle can reach the suppliers"
            hub = await _alternate_hub(deps, state, demand, vehicles, allocation, hazards)
            if hub:
                reason += f". Proposed alternate hub: {hub['name']}, reachable by a safe road"
                await plans.set_status(deps.pool, state["plan_id"], "planning", escalations=[hub])
                await audit("route_planner", "decision", f"Proposed alternate hub {hub['name']}", hub, [f"facility:{hub['facility_id']}"])
            return _stop(state, "unreachable", reason)
        await plans.set_status(deps.pool, state["plan_id"], "planning", escalations=[
            *[x.model_dump(mode="json") for x in allocation.shortfalls], *deferred_escalations(routing, allocation)])
        return {"routing": routing.model_dump(mode="json")}

    async def critic(state: PlanState) -> dict:
        audit = audit_for(state)
        routing = Routing.model_validate(state["routing"])
        # Current hazards (not the planner's snapshot) near any route.
        hazards = await fetch_active_hazards(deps.pool, bbox=_area([pt for r in routing.routes for pt in r.geometry]))
        triage = Triage.model_validate(state["triage"])
        verdict = review(routing, hazards, state["attempt"], datetime.fromisoformat(state["started_at"]),
                         triage.planning_horizon_hours * 3600, s.min_route_clearance_m)
        verdicts = [*state.get("verdicts", []), verdict.model_dump(mode="json")]
        if verdict.passed:
            await audit("critic", "decision", f"Attempt {verdict.attempt}: PASS ({len(hazards)} hazards checked, "
                        f"clearance >= {s.min_route_clearance_m:.0f} m, capacity and horizon OK)", verdict.model_dump(mode="json"),
                        _ids("hazard", verdict.checked_hazard_ids))
            await plans.set_status(deps.pool, state["plan_id"], "planning", critic_attempts=verdict.attempt)
            return {"verdicts": verdicts}
        await audit("critic", "critic_rejection", f"Attempt {verdict.attempt}: REJECTED: " + "; ".join(verdict.violations),
                    verdict.model_dump(mode="json"), _ids("hazard", verdict.checked_hazard_ids))
        await plans.set_status(deps.pool, state["plan_id"], "planning", critic_attempts=verdict.attempt)
        if verdict.attempt >= s.critic_max_attempts:
            return {"verdicts": verdicts, **_stop(state, "failed", f"Critic rejected {verdict.attempt} plans: "
                                                                    + "; ".join(verdict.violations[:3]))}
        return {"verdicts": verdicts, "attempt": verdict.attempt + 1}

    async def brief(state: PlanState) -> dict:
        audit = audit_for(state)
        hazards = [HazardFact.model_validate(h) for h in state["hazards"]]
        triage = Triage.model_validate(state["triage"])
        demand = Demand.model_validate(state["demand"])
        allocation = Allocation.model_validate(state["allocation"])
        routing = Routing.model_validate(state["routing"])
        weight = sum(o.weight_kg for o in allocation.orders)
        reasons = [r for r, hit in [
            ("Sentinel recommends human review", triage.recommend_human_review),
            (f"priority {triage.priority}", triage.priority in ("high", "critical")),
            ("severe/extreme hazard", any(h.severity in ("severe", "extreme") for h in hazards)),
            (f"{weight:,.0f} kg exceeds {s.approval_weight_kg:,.0f} kg", weight > s.approval_weight_kg),
            ("unverified supplier", any(not o.supplier_verified for o in allocation.orders)),
            ("drill", state.get("is_drill", False)),
            ("shortfalls or unserved orders", bool(allocation.shortfalls or routing.unserved_order_keys)),
            ("goods deferred to the next wave", bool(routing.deferred_part_keys)),
        ] if hit]  # fmt: skip
        plan_brief: PlanBrief = await _llm_or_fallback(
            audit, "supervisor", "plan brief",
            (lambda: reasoning.plan_brief(deps.llm, hazards, triage, demand, allocation, routing)) if deps.llm else None,
            lambda reason: fallbacks.brief(allocation, routing, reason))
        if state.get("replan_route_id"):  # these goods were approved already; only new problems need a human
            reasons = ["orders left unserved"] if routing.unserved_order_keys else []
        await plans.set_status(deps.pool, state["plan_id"], "awaiting_approval" if reasons else "approved",
                               summary=plan_brief.summary, requires_approval=bool(reasons))
        await audit("supervisor", "approval_requested" if reasons else "decision",
                    ("Waiting for human approval: " + ", ".join(reasons)) if reasons
                    else "Auto-approved: re-route of goods already approved" if state.get("replan_route_id")
                    else "Auto-approved: low-stakes plan",
                    {"brief": plan_brief.model_dump(), "reasons": reasons})
        return {"brief": plan_brief.model_dump(), "requires_approval": bool(reasons), "approval_reasons": reasons}

    async def approval_gate(state: PlanState) -> dict:
        # No side effects before interrupt(): this node re-runs from the top when resumed.
        if not state.get("requires_approval"):
            return {"decision": {"decision": "approved", "decided_by": "system", "reason": "auto-approved"}}
        decision = interrupt({"plan_id": state["plan_id"], "reasons": state.get("approval_reasons", [])})
        if decision.get("decision") != "approved":
            return {"decision": decision, **_stop(state, "rejected", decision.get("reason") or "rejected by approver")}
        return {"decision": decision}

    async def revalidate(state: PlanState) -> dict:
        audit = audit_for(state)
        problems, routing = await recheck(deps, state, datetime.now(UTC))
        if problems:
            await audit("supervisor", "decision", "Plan is out of date, not dispatching: " + "; ".join(problems[:4]),
                        {"problems": problems})
            return _stop(state, "superseded", "; ".join(problems[:4]))
        await audit("supervisor", "decision", "Re-checked before dispatch: hazards, routes, stock and vehicles still hold")
        return {"routing": routing.model_dump(mode="json")}

    async def dispatcher(state: PlanState) -> dict:
        audit = audit_for(state)
        result = await dispatch(deps, audit, state)
        return {"outcome": "dispatched", "outcome_reason": result}

    async def finish(state: PlanState) -> dict:
        audit = audit_for(state)
        status = state.get("outcome", "failed")
        reason = state.get("outcome_reason", "")
        if status != "dispatched":  # dispatcher already set its own status
            await plans.set_status(deps.pool, state["plan_id"], status,
                                   **({"unreachable_reason": reason} if status in STOPPED_WITH_REASON else {}))
        await audit("supervisor", "node_finished", f"Plan {status}: {reason}", {"status": status})
        if status == "superseded" and not state.get("replan_route_id") and (
                still_active := await data.hazard_facts(deps.pool, state["hazard_ids"])):
            summary, created = await plans.request_plan(
                deps.pool, trigger_kind="replan", trigger_ref=None, hazard_ids=[h.hazard_id for h in still_active],
                requested_by="revalidation", idempotency_key=f"stale:{state['plan_id']}", parent_plan_id=state["plan_id"])
            if created:
                await audit("supervisor", "decision", f"Requested a fresh plan {summary['id']} for the hazards still active")
        return {}

    return {"replan_seed": replan_seed, "sentinel": sentinel, "needs": needs, "procurement": procurement, "route_planner": route_planner,
            "critic": critic, "brief": brief, "approval_gate": approval_gate, "revalidate": revalidate,
            "dispatcher": dispatcher, "finish": finish}
