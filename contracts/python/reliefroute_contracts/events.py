"""Redis Streams event catalog.

Wire format: each stream entry has exactly one field, ``data``, holding the JSON of an
``EventEnvelope``. The stream key equals ``event_type`` (e.g. ``hazard.updated``).
Events are notifications; the database stays the source of truth (geometry, full rows).
Consumers must be idempotent on ``event_id``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .enums import (
    DeviationKind,
    HazardSeverity,
    HazardSource,
    HazardType,
    ItemCode,
    PlanTriggerKind,
    Producer,
    StockReportChannel,
)

SCHEMA_VERSION = 1


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LonLat(_Payload):
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)


# ---------------------------------------------------------------- emitted by B (senses)


class HazardUpdated(_Payload):
    hazard_id: UUID
    change: Literal["created", "updated", "expired", "cancelled"]
    source: HazardSource
    hazard_type: HazardType
    severity: HazardSeverity
    version: int = Field(ge=1, description="Increments on every change to the hazard row")
    bbox: tuple[float, float, float, float] = Field(description="min_lon, min_lat, max_lon, max_lat")
    area_km2: float = Field(ge=0)
    is_drill: bool


class VehicleTelemetry(_Payload):
    telemetry_id: int
    vehicle_id: UUID
    position: LonLat
    recorded_at: datetime = Field(description="Device clock, UTC")
    speed_mps: float | None = Field(default=None, ge=0)
    heading_deg: float | None = Field(default=None, ge=0, lt=360)
    accuracy_m: float | None = Field(default=None, ge=0)


class SupplierStockUpdated(_Payload):
    supplier_id: UUID
    item_code: ItemCode
    quantity: float = Field(ge=0)
    previous_quantity: float | None = Field(default=None, ge=0)
    reported_at: datetime
    reported_via: StockReportChannel


# ---------------------------------------------------------------- emitted by A (brain)


class PlanRequested(_Payload):
    plan_id: UUID
    trigger_kind: PlanTriggerKind
    trigger_ref: str | None = Field(default=None, description="hazard id, event id or free text")
    hazard_ids: list[UUID] = Field(default_factory=list)
    parent_plan_id: UUID | None = None
    requested_by: str = Field(description="'system' or the operator's name")


class PlanApproved(_Payload):
    plan_id: UUID
    approval_id: UUID
    decided_by: str
    decided_at: datetime


class VehicleDeviation(_Payload):
    vehicle_id: UUID
    plan_id: UUID
    route_id: UUID
    kind: DeviationKind
    detected_at: datetime
    last_position: LonLat | None = None
    distance_from_route_m: float | None = Field(default=None, ge=0)
    hazard_id: UUID | None = None


# ---------------------------------------------------------------- envelope

EVENT_TYPES: dict[str, type[_Payload]] = {
    "hazard.updated": HazardUpdated,
    "vehicle.telemetry": VehicleTelemetry,
    "supplier.stock.updated": SupplierStockUpdated,
    "plan.requested": PlanRequested,
    "plan.approved": PlanApproved,
    "vehicle.deviation": VehicleDeviation,
}

EVENT_PRODUCERS: dict[str, Producer] = {
    "hazard.updated": Producer.SENSES,
    "vehicle.telemetry": Producer.SENSES,
    "supplier.stock.updated": Producer.SENSES,
    "plan.requested": Producer.BRAIN,
    "plan.approved": Producer.BRAIN,
    "vehicle.deviation": Producer.BRAIN,
}


class _EnvelopeBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID
    schema_version: Literal[1] = SCHEMA_VERSION
    emitted_at: datetime
    producer: Producer
    dedupe_key: str | None = Field(
        default=None, description="Stable key for semantically identical events (e.g. hazard_id:version)"
    )


class HazardUpdatedEvent(_EnvelopeBase):
    event_type: Literal["hazard.updated"]
    payload: HazardUpdated


class VehicleTelemetryEvent(_EnvelopeBase):
    event_type: Literal["vehicle.telemetry"]
    payload: VehicleTelemetry


class SupplierStockUpdatedEvent(_EnvelopeBase):
    event_type: Literal["supplier.stock.updated"]
    payload: SupplierStockUpdated


class PlanRequestedEvent(_EnvelopeBase):
    event_type: Literal["plan.requested"]
    payload: PlanRequested


class PlanApprovedEvent(_EnvelopeBase):
    event_type: Literal["plan.approved"]
    payload: PlanApproved


class VehicleDeviationEvent(_EnvelopeBase):
    event_type: Literal["vehicle.deviation"]
    payload: VehicleDeviation


EventEnvelope = Annotated[
    Union[
        HazardUpdatedEvent,
        VehicleTelemetryEvent,
        SupplierStockUpdatedEvent,
        PlanRequestedEvent,
        PlanApprovedEvent,
        VehicleDeviationEvent,
    ],
    Field(discriminator="event_type"),
]

envelope_adapter: TypeAdapter[EventEnvelope] = TypeAdapter(EventEnvelope)


def parse_envelope(raw: str | bytes) -> EventEnvelope:
    """Validate a stream entry's ``data`` field. Raises pydantic.ValidationError."""
    return envelope_adapter.validate_json(raw)


def event_json_schema() -> dict:
    """JSON Schema for the whole catalog; contracts/events.schema.json is generated from this."""
    return envelope_adapter.json_schema()
