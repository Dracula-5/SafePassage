"""Rule-based stand-ins used ONLY when the LLM is unavailable (no key, outage, or invalid output
after repair). Every result says it is a fallback; nothing is presented as model reasoning."""

from __future__ import annotations

from app.agents.reasoning import PlanBrief
from app.agents.schemas import Allocation, HazardFact, PopulationEstimate, Routing, Triage

PRIORITY = {"extreme": "critical", "severe": "high", "moderate": "medium"}


def triage(hazards: list[HazardFact], population: PopulationEstimate, reason: str) -> Triage:
    worst = max(hazards, key=lambda h: ["minor", "unknown", "moderate", "severe", "extreme"].index(h.severity)
                if h.severity in ("minor", "unknown", "moderate", "severe", "extreme") else 0)
    return Triage(
        priority=PRIORITY.get(worst.severity, "low"), planning_horizon_hours=24,
        rationale=f"RULE-BASED FALLBACK ({reason}): priority from the worst severity ({worst.severity}); "
                  "default 24 h horizon. Needs human review.",
        key_risks=[f"LLM triage unavailable: {reason}"]
                  + (["population coverage below 50%"] if population.coverage < 0.5 else []),
        recommend_human_review=True,
    )  # fmt: skip


def brief(allocation: Allocation, routing: Routing, reason: str) -> PlanBrief:
    tons = sum(o.weight_kg for o in allocation.orders) / 1000
    return PlanBrief(
        summary=f"RULE-BASED SUMMARY ({reason}): {len(allocation.orders)} orders, {tons:.1f} t, "
                f"{len(routing.routes)} vehicle routes, {len(allocation.shortfalls)} shortfalls, "
                f"{len(routing.unserved_order_keys)} unserved order parts.",
        risks=[s.reason for s in allocation.shortfalls][:6],
        approval_notes="LLM brief unavailable; review orders and routes directly.",
    )  # fmt: skip
