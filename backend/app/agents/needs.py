"""Needs: population (lower bound from v_places) x cited standards x horizon -> demand per relief point."""

from __future__ import annotations

import math

from .schemas import Demand, DemandLine, PopulationEstimate, ReliefPoint
from .standards import STANDARDS


def compute_demand(population: PopulationEstimate, points: list[ReliefPoint], horizon_days: float) -> Demand:
    """Split the zone's demand evenly across the relief points (no capacities are known for them).

    Quantities are rounded up to whole units. With no relief point or no population there is
    no demand line; the caller escalates instead of inventing one.
    """
    shared = [p.model_copy(update={"share": 1 / len(points)}) for p in points] if points else []
    lines: list[DemandLine] = []
    if population.population_lower_bound > 0:
        for point in shared:
            people = population.population_lower_bound * point.share
            for std in STANDARDS:
                lines.append(DemandLine(
                    destination_facility_id=point.facility_id, item_code=std.item,
                    quantity=math.ceil(people * std.per_person_per_day * horizon_days),
                    basis=f"{people:.0f} people x {std.per_person_per_day} {std.item.value}/person/day x "
                          f"{horizon_days:g} days ({std.source})",
                ))  # fmt: skip
    return Demand(population=population, horizon_days=horizon_days, relief_points=shared, lines=lines)


def still_needed(demand: Demand, ordered: dict[tuple[str, str], float]) -> Demand:
    """Take off what is already ordered for this alert, so a later wave never sends the same goods twice."""
    left = [line.model_copy(update={"quantity": line.quantity - ordered.get((line.destination_facility_id, line.item_code.value), 0)})
            for line in demand.lines]  # fmt: skip
    return demand.model_copy(update={"lines": [line for line in left if line.quantity > 0]})
