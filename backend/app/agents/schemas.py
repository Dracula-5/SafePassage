"""Typed pieces of plan state. Everything the graph checkpoints is one of these, dumped to JSON."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from reliefroute_contracts.enums import ItemCode


class HazardFact(BaseModel):
    hazard_id: str
    source: str
    hazard_type: str
    severity: str
    headline: str
    area_desc: str | None
    area_km2: float
    is_drill: bool
    is_stale: bool
    expires_at: datetime | None


class Triage(BaseModel):
    """Sentinel output (LLM). Bounded fields only; no coordinates or quantities."""

    priority: Literal["low", "medium", "high", "critical"]
    planning_horizon_hours: int = Field(ge=12, le=72, description="How many hours of supplies to plan for")
    rationale: str = Field(min_length=20, max_length=800)
    key_risks: list[str] = Field(default_factory=list, max_length=5)
    recommend_human_review: bool


class PopulationEstimate(BaseModel):
    """Sum of census figures (OSM `population` tags) of settlements whose point lies in the zone.

    Not a strict bound either way: untagged settlements add nothing (undercount), while cities and
    towns are scaled by the zone's share of their mandal, assuming even density (an estimate).
    `places_with_population / places_total` is reported next to it everywhere.
    """

    population_lower_bound: int = Field(ge=0, description="Historic name; see class docstring")
    population_unscaled: int = Field(default=0, ge=0, description="Sum before scaling, for the audit trail")
    places_with_population: int = Field(ge=0)
    places_total: int = Field(ge=0)
    places_scaled: int = Field(default=0, ge=0, description="Cities/towns only partly inside the zone")
    sources: list[str]

    @property
    def coverage(self) -> float:
        return self.places_with_population / self.places_total if self.places_total else 0.0


class ReliefPoint(BaseModel):
    facility_id: str
    name: str
    kind: str
    lon: float
    lat: float
    distance_to_hazard_m: float
    share: float = Field(ge=0, le=1, description="Fraction of the zone's demand sent here")


class DemandLine(BaseModel):
    destination_facility_id: str
    item_code: ItemCode
    quantity: float = Field(ge=0)
    basis: str


class Demand(BaseModel):
    population: PopulationEstimate
    horizon_days: float
    relief_points: list[ReliefPoint]
    lines: list[DemandLine]


class OrderLineDraft(BaseModel):
    item_code: ItemCode
    quantity: float = Field(gt=0)


class OrderDraft(BaseModel):
    order_key: str
    supplier_id: str
    supplier_name: str
    supplier_verified: bool
    supplier_facility_id: str
    supplier_lon: float
    supplier_lat: float
    destination_facility_id: str
    lines: list[OrderLineDraft]
    weight_kg: float = Field(gt=0)
    travel_s: float | None
    onboard: bool = Field(default=False, description="Already on the truck (replans): no pickup, no new PO")


class Shortfall(BaseModel):
    destination_facility_id: str
    item_code: ItemCode
    quantity_short: float = Field(gt=0)
    reason: str


class Allocation(BaseModel):
    orders: list[OrderDraft]
    shortfalls: list[Shortfall]


class RouteStop(BaseModel):
    facility_id: str
    name: str
    kind: Literal["pickup", "dropoff"]
    eta: datetime
    order_keys: list[str]
    lon: float
    lat: float


class PlannedRoute(BaseModel):
    vehicle_id: str
    vehicle_label: str
    driver_language: str
    order_keys: list[str]
    stops: list[RouteStop]
    geometry: list[tuple[float, float]]
    distance_m: float
    duration_s: float
    load_kg: float
    peak_load_kg: float
    capacity_kg: float


class OrderPart(BaseModel):
    """An order, or a truck-sized part of one (key 'order_key#n' when split)."""

    part_key: str
    order_key: str
    lines: list[OrderLineDraft]
    weight_kg: float


class Routing(BaseModel):
    routes: list[PlannedRoute]
    parts: list[OrderPart] = Field(default_factory=list)
    unserved_order_keys: list[str]
    deferred_part_keys: list[str] = Field(default_factory=list, description="Beyond this wave's fleet capacity")
    onboard_part_keys: list[str] = Field(default_factory=list, description="Loaded before this route began")
    margin_m: float
    vehicles_considered: int


class CriticVerdict(BaseModel):
    attempt: int = Field(ge=1)
    passed: bool
    violations: list[str]
    checked_hazard_ids: list[str]
    min_clearance_m: dict[str, float | None] = Field(default_factory=dict, description="vehicle_id -> metres")
    created_at: datetime
