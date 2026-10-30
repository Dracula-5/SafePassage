"""Drift guards: Python enums == SQL enums == OpenAPI enums; schema file == models."""

import json
import re
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import yaml
from openapi_spec_validator import validate
from pydantic import ValidationError
from reliefroute_contracts import enums
from reliefroute_contracts.events import EVENT_TYPES, parse_envelope

from .conftest import REPO_ROOT

CONTRACTS = REPO_ROOT / "contracts"

# SQL type name -> Python enum
SQL_ENUMS = {
    "hazard_source": enums.HazardSource,
    "hazard_type": enums.HazardType,
    "hazard_severity": enums.HazardSeverity,
    "hazard_status": enums.HazardStatus,
    "facility_kind": enums.FacilityKind,
    "facility_source": enums.FacilitySource,
    "item_code": enums.ItemCode,
    "stock_report_channel": enums.StockReportChannel,
    "vehicle_status": enums.VehicleStatus,
    "plan_trigger_kind": enums.PlanTriggerKind,
    "plan_status": enums.PlanStatus,
    "route_status": enums.RouteStatus,
    "order_status": enums.OrderStatus,
    "approval_decision": enums.ApprovalDecision,
    "agent_node": enums.AgentNode,
    "agent_event_type": enums.AgentEventType,
    "deviation_kind": enums.DeviationKind,
}


def _sql_enums() -> dict[str, list[str]]:
    sql = (CONTRACTS / "db" / "000_contract_types.sql").read_text()
    found = re.findall(r"CREATE TYPE (\w+) AS ENUM \(([^)]*)\)", sql)
    return {name: re.findall(r"'([^']*)'", values) for name, values in found}


def test_sql_enums_match_python():
    sql = _sql_enums()
    assert set(sql) == set(SQL_ENUMS)
    for name, py_enum in SQL_ENUMS.items():
        assert sql[name] == [m.value for m in py_enum], name


@pytest.fixture(scope="module")
def openapi() -> dict:
    return yaml.safe_load((CONTRACTS / "openapi.yaml").read_text())


def test_openapi_is_valid(openapi):
    validate(openapi)


def test_openapi_enums_match_python(openapi):
    schemas = openapi["components"]["schemas"]
    for py_enum in SQL_ENUMS.values():
        name = py_enum.__name__
        assert schemas[name]["enum"] == [m.value for m in py_enum], name


def test_every_item_has_a_unit():
    assert set(enums.ITEM_UNITS) == set(enums.ItemCode)


def test_event_schema_file_is_current():
    import gen_event_schema

    assert (CONTRACTS / "events.schema.json").read_text() == gen_event_schema.render()


def _envelope(event_type: str, payload: dict) -> str:
    return json.dumps(
        {
            "event_id": str(uuid4()),
            "event_type": event_type,
            "schema_version": 1,
            "emitted_at": datetime.now(UTC).isoformat(),
            "producer": "senses",
            "payload": payload,
        }
    )


def test_envelope_dispatches_on_event_type():
    raw = _envelope(
        "vehicle.telemetry",
        {"telemetry_id": 1, "vehicle_id": str(uuid4()), "position": {"lon": 85.8, "lat": 20.3},
         "recorded_at": datetime.now(UTC).isoformat()},
    )
    env = parse_envelope(raw)
    assert env.event_type == "vehicle.telemetry"
    assert isinstance(env.payload, EVENT_TYPES["vehicle.telemetry"])


@pytest.mark.parametrize(
    "event_type,payload",
    [
        ("vehicle.telemetry", {"telemetry_id": 1, "vehicle_id": str(uuid4()),
                               "position": {"lon": 200, "lat": 0}, "recorded_at": "2026-10-30T00:00:00Z"}),
        ("supplier.stock.updated", {"supplier_id": str(uuid4()), "item_code": "GOLD_BARS", "quantity": 1,
                                    "reported_at": "2026-10-30T00:00:00Z", "reported_via": "portal"}),
        ("plan.approved", {"plan_id": str(uuid4()), "approval_id": str(uuid4()), "decided_by": "x",
                           "decided_at": "2026-10-30T00:00:00Z", "unexpected": 1}),
        ("no.such.event", {}),
    ],
)
def test_envelope_rejects_invalid(event_type, payload):
    with pytest.raises(ValidationError):
        parse_envelope(_envelope(event_type, payload))
