"""LLM-backed reasoning steps. Prompts contain only facts read from the DB or computed by the
solvers; outputs are bounded Pydantic models. The LLM never supplies coordinates, distances,
stock or quantities."""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field, field_validator

from app.llm.client import LLMClient, LLMResult

from .schemas import Allocation, Demand, HazardFact, PopulationEstimate, Routing, Triage

LANGUAGE_NAMES = {"te": "Telugu", "hi": "Hindi", "ta": "Tamil", "kn": "Kannada", "or": "Odia", "en": "English"}

SENTINEL_SYSTEM = """You are the Sentinel of a disaster-relief logistics system for Andhra Pradesh, India.
Triage the hazards below for relief planning. Use ONLY the facts given. Do not invent numbers,
places or coordinates. A hazard with is_drill=true is a what-if exercise drawn by an operator:
triage it exactly as if it were real (that is its purpose) and name the drill in key_risks. Choose a planning horizon (12-72 h) that matches hazard type, severity
and expiry. Flag data quality problems (stale sources, drills, low population coverage) in
key_risks. Recommend human review when severity is severe/extreme, data is stale or coverage is low."""


async def triage(llm: LLMClient, hazards: list[HazardFact], population: PopulationEstimate,
                 relief_points: int) -> LLMResult[Triage]:
    facts = {
        "hazards": [h.model_dump(mode="json") for h in hazards],
        "population_in_zone": {"lower_bound": population.population_lower_bound,
                               "settlements_with_census_figure": population.places_with_population,
                               "settlements_total": population.places_total,
                               "coverage_ratio": round(population.coverage, 3)},
        "safe_relief_points_found_nearby": relief_points,
    }  # fmt: skip
    return await llm.generate(SENTINEL_SYSTEM, "Facts:\n" + json.dumps(facts, indent=1), Triage)


class PlanBrief(BaseModel):
    summary: str = Field(min_length=20, max_length=900, description="What will happen, for the approver")
    risks: list[str] = Field(default_factory=list, max_length=6)
    approval_notes: str = Field(max_length=500, description="What the approver should check before approving")


BRIEF_SYSTEM = """You brief a human duty officer who must approve or reject a relief dispatch plan.
Summarise the plan in plain English using ONLY the facts provided (quote numbers exactly as given).
State shortfalls, unreachable orders, unverified suppliers, drills and data-quality issues plainly."""


async def plan_brief(llm: LLMClient, hazards: list[HazardFact], triage_out: Triage, demand: Demand,
                     allocation: Allocation, routing: Routing) -> LLMResult[PlanBrief]:
    facts = {
        "hazards": [{"headline": h.headline, "severity": h.severity, "is_drill": h.is_drill, "is_stale": h.is_stale}
                    for h in hazards],
        "triage": triage_out.model_dump(),
        "population_lower_bound": demand.population.population_lower_bound,
        "population_coverage_ratio": round(demand.population.coverage, 3),
        "relief_points": [p.name for p in demand.relief_points],
        "orders": [{"supplier": o.supplier_name, "verified": o.supplier_verified, "weight_kg": round(o.weight_kg),
                    "lines": [f"{l.quantity:g} {l.item_code.value}" for l in o.lines]} for o in allocation.orders],
        "shortfalls": [s.model_dump(mode="json") for s in allocation.shortfalls],
        "routes": [{"vehicle": r.vehicle_label, "km": round(r.distance_m / 1000, 1), "hours": round(r.duration_s / 3600, 1),
                    "stops": [f"{s.kind} {s.name}" for s in r.stops]} for r in routing.routes],
        "unserved_orders": routing.unserved_order_keys,
        "order_parts_deferred_to_next_wave": len(routing.deferred_part_keys),
    }  # fmt: skip
    return await llm.generate(BRIEF_SYSTEM, "Plan facts:\n" + json.dumps(facts, indent=1, default=str), PlanBrief)


class Translation(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


def numbers_in(text: str) -> list[str]:
    return sorted(re.findall(r"\d+(?:[.:]\d+)?", text))


def translation_schema(source: str) -> type[Translation]:
    """Schema whose validation fails when any number of `source` is changed or dropped, so the
    LLM client's repair loop sends the exact discrepancy back to the model."""
    expected = numbers_in(source)

    class CheckedTranslation(Translation):
        @field_validator("text")
        @classmethod
        def _numbers_kept(cls, text: str) -> str:
            if numbers_in(text) != expected:
                raise ValueError(f"numbers must stay exactly {expected}; translation has {numbers_in(text)}")
            return text

    return CheckedTranslation


async def translate_instructions(llm: LLMClient, english: str, language: str) -> LLMResult[Translation]:
    name = LANGUAGE_NAMES.get(language, language)
    system = (f"Translate driver instructions from English into {name} for a truck driver. Keep every number, "
              "time and item code exactly as written (Western digits). Keep it short and clear for reading aloud.")  # fmt: skip
    return await llm.generate(system, english, translation_schema(english))
