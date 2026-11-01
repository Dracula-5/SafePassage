"""Who decides: these rules, or a duty officer.

A plan goes out by itself when nothing about it is unclear. Any reason returned here sends it to a person
instead. This is plain code on purpose: a model's words can add a person (`recommend_human_review`) but can
never remove one. A big job is not an unclear one: only what leaves now is weighed, what can move should move,
and `graph/waves.py` sends the rest when these trucks finish.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import SIMULATED_LABEL

from .schemas import Allocation, HazardFact, Routing, Triage


@dataclass(frozen=True)
class Limits:
    max_kg: float             # more than this leaving at once is a decision for a person
    max_unserved: float       # share of the goods in this wave that no truck can reach
    min_clearance_m: float    # a route closer to a hazard than this is too close to wave through


def reasons_for_a_person(hazards: list[HazardFact], triage: Triage, allocation: Allocation, routing: Routing,
                         verdicts: list[dict], attempt: int, limits: Limits, crosses_warning: bool = False) -> list[str]:
    """Empty means the rules approve it. Short, because a duty officer reads these under pressure."""
    real_alert = any(not h.is_drill for h in hazards)
    planned = [p for p in routing.parts if p.part_key not in routing.deferred_part_keys]
    planned_kg = sum(p.weight_kg for p in planned)
    unserved_kg = sum(p.weight_kg for p in planned if p.part_key in routing.unserved_order_keys)
    moving_kg = planned_kg - unserved_kg
    clearances = ((verdicts or [{}])[-1].get("min_clearance_m") or {}).values()
    closest = min((c for c in clearances if c is not None), default=None)
    checks = [
        ("Extreme real alert", real_alert and any(h.severity == "extreme" for h in hazards)),
        ("Route crosses a wide warning area", crosses_warning),
        ("Real alert, but simulated trucks", real_alert and any(r.vehicle_label.startswith(SIMULATED_LABEL) for r in routing.routes)),
        ("Hazard data is out of date", any(h.is_stale for h in hazards)),
        ("AI assessment unavailable", triage.rationale.startswith("RULE-BASED")),
        ("AI is unsure", triage.recommend_human_review),
        ("Unverified supplier", any(not o.supplier_verified for o in allocation.orders)),
        (f"Over {limits.max_kg / 1000:,.0f} t moving at once", moving_kg > limits.max_kg),
        (f"No truck for over {limits.max_unserved:.0%} of the goods", planned_kg > 0 and unserved_kg / planned_kg > limits.max_unserved),
        (f"Route under {limits.min_clearance_m:.0f} m from a hazard", closest is not None and closest < limits.min_clearance_m),
        ("Safety check rejected the first route", attempt > 1),
    ]
    return [reason for reason, applies in checks if applies]
