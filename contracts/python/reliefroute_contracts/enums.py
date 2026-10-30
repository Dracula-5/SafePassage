"""Enumerations shared by DB, events and REST.

These must match the Postgres enum types in contracts/db/schema.sql;
backend/tests/brain/test_contracts.py fails if they drift.
"""

from enum import StrEnum


class HazardSource(StrEnum):
    SACHET = "sachet"          # NDMA SACHET CAP feed
    IMD = "imd"
    OPEN_METEO = "open_meteo"
    GDACS = "gdacs"
    FIRMS = "firms"            # NASA FIRMS active fire
    CWC = "cwc"                # CWC / India-WRIS river levels
    DRILL = "drill"            # judge-drawn what-if zone, always labelled DRILL


class HazardType(StrEnum):
    FLOOD = "flood"
    HEAVY_RAIN = "heavy_rain"
    CYCLONE = "cyclone"
    STORM = "storm"
    LANDSLIDE = "landslide"
    WILDFIRE = "wildfire"
    HEATWAVE = "heatwave"
    EARTHQUAKE = "earthquake"
    TSUNAMI = "tsunami"
    OTHER = "other"


class HazardSeverity(StrEnum):
    """CAP 1.2 severity values (lower-cased)."""

    MINOR = "minor"
    MODERATE = "moderate"
    SEVERE = "severe"
    EXTREME = "extreme"
    UNKNOWN = "unknown"


class HazardStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class FacilityKind(StrEnum):
    HOSPITAL = "hospital"
    SHELTER = "shelter"
    WAREHOUSE = "warehouse"
    GODOWN = "godown"          # e.g. FCI storage depots from data.gov.in
    SCHOOL = "school"
    FUEL = "fuel"
    DEPOT = "depot"            # vehicle base / relief hub
    OTHER = "other"


class FacilitySource(StrEnum):
    OSM = "osm"
    DATAGOV = "datagov"
    PORTAL = "portal"          # registered by a supplier through the check-in portal


class ItemCode(StrEnum):
    """Relief items. Units are fixed per code; quantities are always in this unit."""

    WATER_L = "WATER_L"            # litre of drinking water
    RICE_KG = "RICE_KG"            # kg
    PULSES_KG = "PULSES_KG"        # kg
    RTE_MEAL = "RTE_MEAL"          # one ready-to-eat meal pack
    TARPAULIN = "TARPAULIN"        # one sheet
    BLANKET = "BLANKET"            # one blanket
    HYGIENE_KIT = "HYGIENE_KIT"    # one family hygiene kit
    MEDICAL_KIT = "MEDICAL_KIT"    # one first-aid / medical kit
    ORS_SACHET = "ORS_SACHET"      # one oral rehydration salts sachet


ITEM_UNITS: dict[ItemCode, str] = {
    ItemCode.WATER_L: "litre",
    ItemCode.RICE_KG: "kg",
    ItemCode.PULSES_KG: "kg",
    ItemCode.RTE_MEAL: "pack",
    ItemCode.TARPAULIN: "sheet",
    ItemCode.BLANKET: "piece",
    ItemCode.HYGIENE_KIT: "kit",
    ItemCode.MEDICAL_KIT: "kit",
    ItemCode.ORS_SACHET: "sachet",
}


class StockReportChannel(StrEnum):
    PORTAL = "portal"
    WHATSAPP = "whatsapp"
    API = "api"


class VehicleStatus(StrEnum):
    AVAILABLE = "available"
    ASSIGNED = "assigned"
    EN_ROUTE = "en_route"
    MAINTENANCE = "maintenance"
    # "offline" is not stored; it is derived in v_vehicle_status from last_seen_at.


class PlanTriggerKind(StrEnum):
    HAZARD = "hazard"
    MANUAL = "manual"
    REPLAN = "replan"
    DRILL = "drill"


class PlanStatus(StrEnum):
    PENDING = "pending"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISPATCHED = "dispatched"
    SUPERSEDED = "superseded"      # replaced by a replan
    UNREACHABLE = "unreachable"    # no safe route exists; alternate hub proposed
    FAILED = "failed"


class RouteStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class OrderStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    DISPATCHED = "dispatched"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class AgentNode(StrEnum):
    SUPERVISOR = "supervisor"
    SENTINEL = "sentinel"
    NEEDS = "needs"
    PROCUREMENT = "procurement"
    ROUTE_PLANNER = "route_planner"
    CRITIC = "critic"
    DISPATCHER = "dispatcher"
    REPLANNER = "replanner"
    HUMAN = "human"


class AgentEventType(StrEnum):
    NODE_STARTED = "node_started"
    NODE_FINISHED = "node_finished"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    DECISION = "decision"
    CRITIC_REJECTION = "critic_rejection"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_DECIDED = "approval_decided"
    DISPATCH = "dispatch"
    DATA_STALE = "data_stale"      # a fallback source was used
    ERROR = "error"


class DeviationKind(StrEnum):
    OFF_ROUTE = "off_route"
    STALLED = "stalled"
    OFFLINE = "offline"
    HAZARD_ON_ROUTE = "hazard_on_route"


class Producer(StrEnum):
    BRAIN = "brain"
    SENSES = "senses"


# Thresholds both sides must agree on.
VEHICLE_OFFLINE_AFTER_SECONDS = 120
