"""Input validation sweep over every endpoint a client can write to."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from app.api_senses.router import MAX_DRILL_VERTICES, DrillRequest
from app.core.config import get_common_settings
from app.ingestion.settings import get_senses_settings
from app.telemetry.router import TelemetryBatch, TelemetryFix

from pydantic import ValidationError

SQUARE = [[80.6, 16.5], [80.7, 16.5], [80.7, 16.6], [80.6, 16.6], [80.6, 16.5]]


def drill(coords, **extra):
    return {"geometry": {"type": "Polygon", "coordinates": [coords]}, "hazard_type": "flood", "severity": "severe",
            "label": "x", **extra}  # fmt: skip


@pytest.mark.parametrize("coords", [
    [[80.6, 16.5], [80.7, 16.5]],                                            # not a ring
    [[80.6, 16.5], [80.7, 16.6], [80.7, 16.5], [80.6, 16.6], [80.6, 16.5]],  # self-intersecting
    [[80.6, 16.5], [80.7, 16.5], [80.8, 16.5], [80.6, 16.5]],                # no area
    [[200, 16.5], [201, 16.5], [201, 17], [200, 16.5]],                      # off the planet
], ids=["two_points", "bowtie", "zero_area", "out_of_range"])
def test_bad_drill_shapes_are_refused(coords):
    with pytest.raises(ValidationError):
        DrillRequest(**drill(coords))


def test_drill_cannot_have_unbounded_vertices():
    import math

    n = MAX_DRILL_VERTICES + 5
    ring = [[80.6 + 0.1 * math.cos(2 * math.pi * i / n), 16.5 + 0.1 * math.sin(2 * math.pi * i / n)] for i in range(n)]
    with pytest.raises(ValidationError, match="vertices"):
        DrillRequest(**drill([*ring, ring[0]]))


@pytest.mark.parametrize("extra", [{"ttl_minutes": 1}, {"ttl_minutes": 100_000}, {"label": ""}, {"severity": "biblical"}])
def test_drill_field_limits(extra):
    with pytest.raises(ValidationError):
        DrillRequest(**drill(SQUARE, **extra))


def test_a_valid_drill_is_accepted():
    assert DrillRequest(**drill(SQUARE)).label == "x"


@pytest.mark.parametrize("fix", [
    {"lon": 181, "lat": 16},
    {"lon": 80, "lat": -91},
    {"lon": float("nan"), "lat": 16},
    {"lon": 80, "lat": 16, "speed_mps": -1},
    {"lon": 80, "lat": 16, "heading_deg": 360},
    {"lon": 80, "lat": 16, "accuracy_m": -5},
], ids=["lon", "lat", "nan", "speed", "heading", "accuracy"])
def test_impossible_gps_fixes_are_refused(fix):
    with pytest.raises(ValidationError):
        TelemetryFix(recorded_at=datetime.now(UTC), **fix)


@pytest.mark.parametrize("when", [
    datetime.now(UTC) + timedelta(hours=1),
    datetime.now(UTC) - timedelta(days=30),
    datetime(2026, 10, 31, 6, 0),
], ids=["future", "too_old", "no_timezone"])
def test_bad_fix_times_are_refused(when):
    with pytest.raises(ValidationError):
        TelemetryFix(lon=80, lat=16, recorded_at=when)


def test_telemetry_batch_size_is_limited():
    fix = {"lon": 80, "lat": 16, "recorded_at": datetime.now(UTC)}
    with pytest.raises(ValidationError):
        TelemetryBatch(vehicle_id=uuid4(), fixes=[])
    with pytest.raises(ValidationError):
        TelemetryBatch(vehicle_id=uuid4(), fixes=[fix] * 501)


@pytest.mark.integration
async def test_endpoint_rejections_and_operator_token(live_env, monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("REGION_BBOX", "76.6,12.5,84.9,20.1")
    get_senses_settings.cache_clear()
    app = create_app(("app.api_senses.router", "app.api_brain.router"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for path, body in [
            ("/api/vehicles", {"label": "", "driver_language": "te", "capacity_kg": 100}),
            ("/api/vehicles", {"label": "x", "driver_language": "te", "capacity_kg": -1}),
            ("/api/vehicles", {"label": "x", "driver_language": "te", "capacity_kg": 100, "driver_phone": "12345"}),
            ("/api/telemetry", {"vehicle_id": "not-a-uuid", "fixes": []}),
            ("/portal/api/register", {"name": "x"}),
            ("/portal/api/register", {"name": "Depot", "lon": 80}),
            ("/api/plans/trigger", {"requested_by": ""}),
        ]:
            headers = {"X-Device-Token": "x"} if path == "/api/telemetry" else {}
            assert (await client.post(path, json=body, headers=headers)).status_code == 422, (path, body)

        assert (await client.get("/api/hazards", params={"bbox": "1,2,3"})).status_code == 422
        assert (await client.get("/api/hazards", params={"bbox": "9,9,1,1"})).status_code == 422
        assert (await client.get("/api/facilities", params={"kind": "volcano"})).status_code == 422
        assert (await client.get("/portal/api/facilities", params={"q": "x"})).status_code == 422
        assert (await client.post("/portal/api/stock", json={"lines": []}, headers={"X-Supplier-Token": "x"})).status_code == 422
        assert (await client.post("/api/drill", json=drill([[10, 50], [10.1, 50], [10.1, 50.1], [10, 50]]))).status_code == 422
        assert (await client.delete(f"/api/drill/{uuid4()}")).status_code == 404
        assert (await client.get("/driver/../../.env")).status_code == 404

        monkeypatch.setenv("OPERATOR_TOKEN", "s3cret")
        get_common_settings.cache_clear()
        assert (await client.post("/api/drill", json=drill(SQUARE))).status_code == 401
        assert (await client.post(f"/api/plans/{uuid4()}/approve", json={"decision": "approved", "decided_by": "x"})).status_code == 401
        assert (await client.post("/api/drill", json=drill(SQUARE), headers={"X-Operator-Token": "wrong"})).status_code == 401
        assert (await client.post("/api/drill", json=drill(SQUARE), headers={"X-Operator-Token": "s3cret"})).status_code == 201
        assert (await client.get("/api/hazards")).status_code == 200  # reading stays open
    monkeypatch.delenv("OPERATOR_TOKEN")
    get_common_settings.cache_clear()
    get_senses_settings.cache_clear()
