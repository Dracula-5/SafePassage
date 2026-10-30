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
from app.agents.procurement import StockRow, allocate
from app.agents.reasoning import PlanBrief
from app.agents.schemas import Allocation, Demand, HazardFact, Routing, Triage
from app.core.resilience import SourceUnavailable
from app.core.settings_brain import BrainSettings
from app.dispatch.messaging import Messenger
from app.llm.client import LLMClient, LLMOutputError
from app.routing.repository import fetch_active_hazards
from app.routing.valhalla import Location, ValhallaClient
from app.solvers.geometry import exclusion_rings

from . import fallbacks, plans
from .audit import Audit
from .dispatcher import dispatch


class PlanState(TypedDict, total=False):
    plan_id: str
    thread_id: str
    hazard_ids: list[str]
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


def _stop(state: PlanState, status: str, reason: str) -> dict:
    return {"outcome": status, "outcome_reason": reason}


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
                    f"(untagged settlements add none; a town/city figure counts in full when its centre lies in the zone); "
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
        travel: dict[tuple[str, str], float | None] = {}
        if stock:
            hazards = await fetch_active_hazards(deps.pool)
            rings = exclusion_rings(hazards)
            suppliers = sorted({(x.supplier_id, x.lon, x.lat) for x in stock})
            matrix = await deps.valhalla.matrix([Location(lon=lo, lat=la) for _, lo, la in suppliers],
                                                [Location(lon=p.lon, lat=p.lat) for p in demand.relief_points], exclude=rings)
            for i, (sid, _, _) in enumerate(suppliers):
                for j, p in enumerate(demand.relief_points):
                    travel[(sid, p.facility_id)] = matrix.durations_s[i][j]
            await audit("procurement", "tool_call", f"Valhalla matrix {len(suppliers)}x{len(demand.relief_points)} around "
                        f"{len(hazards)} active hazard(s)", {"durations_s": matrix.durations_s},
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
        rows = await data.usable_vehicles(deps.pool)
        offline = await data.offline_vehicle_count(deps.pool)
        await audit("route_planner", "tool_result", f"{len(rows)} online vehicle(s) with a GPS fix; {offline} offline",
                    {"vehicles": [dict(r) for r in rows]}, _ids("vehicle", [r["id"] for r in rows]))
        if not rows:
            return _stop(state, "failed", f"no online vehicle with a GPS fix ({offline} offline): escalated to humans")
        vehicles = [VehicleIn(vehicle_id=r["id"], label=r["label"], capacity_kg=r["capacity_kg"],
                              driver_language=r["driver_language"], lon=r["lon"], lat=r["lat"]) for r in rows]  # fmt: skip
        sites = {p.facility_id: Site(p.facility_id, p.name, p.lon, p.lat) for p in demand.relief_points}
        for o in allocation.orders:
            sites[o.supplier_facility_id] = Site(o.supplier_facility_id, o.supplier_name, o.supplier_lon, o.supplier_lat)
        hazards = await fetch_active_hazards(deps.pool)
        triage = Triage.model_validate(state["triage"])
        routing = await plan_routes(deps.valhalla, allocation.orders, vehicles, sites, hazards,
                                    datetime.fromisoformat(state["started_at"]), triage.planning_horizon_hours * 3600,
                                    state["attempt"])  # fmt: skip
        await audit("route_planner", "decision",
                    f"Attempt {state['attempt']}: {len(routing.routes)} route(s), {len(routing.unserved_order_keys)} "
                    f"unserved order part(s), exclusion margin {routing.margin_m:.0f} m around {len(hazards)} hazard(s)",
                    routing.model_dump(mode="json", exclude={"routes": {"__all__": {"geometry"}}}),
                    _ids("hazard", [h.hazard_id for h in hazards]) + _ids("vehicle", [r.vehicle_id for r in routing.routes]))
        if not routing.routes:
            return _stop(state, "unreachable", "no safe road route for any order: every path crosses an active hazard "
                                               "or no vehicle can reach the suppliers")
        return {"routing": routing.model_dump(mode="json")}

    async def critic(state: PlanState) -> dict:
        audit = audit_for(state)
        routing = Routing.model_validate(state["routing"])
        hazards = await fetch_active_hazards(deps.pool)  # current hazards, not the planner's snapshot
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
        ] if hit]  # fmt: skip
        plan_brief: PlanBrief = await _llm_or_fallback(
            audit, "supervisor", "plan brief",
            (lambda: reasoning.plan_brief(deps.llm, hazards, triage, demand, allocation, routing)) if deps.llm else None,
            lambda reason: fallbacks.brief(allocation, routing, reason))
        await plans.set_status(deps.pool, state["plan_id"], "awaiting_approval" if reasons else "approved",
                               summary=plan_brief.summary, requires_approval=bool(reasons))
        await audit("supervisor", "approval_requested" if reasons else "decision",
                    ("Waiting for human approval: " + ", ".join(reasons)) if reasons else "Auto-approved: low-stakes plan",
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
                                   **({"unreachable_reason": reason} if status in ("unreachable", "failed") else {}))
        await audit("supervisor", "node_finished", f"Plan {status}: {reason}", {"status": status})
        return {}

    return {"sentinel": sentinel, "needs": needs, "procurement": procurement, "route_planner": route_planner,
            "critic": critic, "brief": brief, "approval_gate": approval_gate, "dispatcher": dispatcher, "finish": finish}
