"""Critic: independent checks of a routing against the CURRENT hazards (re-read from the DB).

Rejects with exact, human-readable violations; the planner retries with a wider margin.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.solvers.geometry import HazardShape, verify_route

from .schemas import CriticVerdict, Routing


def review(routing: Routing, hazards: list[HazardShape], attempt: int, start: datetime, horizon_s: int,
           min_clearance_m: float) -> CriticVerdict:
    violations: list[str] = []
    clearance: dict[str, float | None] = {}
    deadline = start + timedelta(seconds=horizon_s)
    for route in routing.routes:
        label = route.vehicle_label
        verdict = verify_route(route.geometry, hazards, min_clearance_m=min_clearance_m)
        violations += [f"{label}: {v}" for v in verdict.explain()]
        clearance[route.vehicle_id] = verdict.min_clearance_m
        if route.peak_load_kg > route.capacity_kg + 1e-6:
            violations.append(f"{label}: peak load {route.peak_load_kg:.0f} kg exceeds capacity {route.capacity_kg:.0f} kg")
        late = [s for s in route.stops if s.eta > deadline]
        if late:
            violations.append(f"{label}: {len(late)} stop(s) arrive after the planning horizon ({deadline.isoformat()})")
        pickups: set[str] = set(routing.onboard_part_keys)
        for stop in route.stops:
            if stop.kind == "pickup":
                pickups.update(stop.order_keys)
            elif missing := [k for k in stop.order_keys if k not in pickups]:
                violations.append(f"{label}: drops off {missing} before picking them up")
    return CriticVerdict(attempt=attempt, passed=not violations, violations=violations,
                         checked_hazard_ids=[h.hazard_id for h in hazards], min_clearance_m=clearance,
                         created_at=datetime.now(UTC))  # fmt: skip
