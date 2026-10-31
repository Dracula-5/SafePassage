"""Gate 2 B endpoints on real PostGIS + Redis: vehicles/telemetry, supplier portal, SSE."""

import asyncio
import base64
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import yaml
from reliefroute_contracts.events import parse_envelope

from app.core import bus
from app.ingestion.settings import get_senses_settings
from app.suppliers.router import twilio_signature_valid
from app.suppliers.service import parse_whatsapp_stock

pytestmark = pytest.mark.integration
REPO = Path(__file__).resolve().parents[3]


def test_senses_router_covers_contract():
    from app.main import create_app

    spec = yaml.safe_load((REPO / "contracts" / "openapi.yaml").read_text())
    wanted = {(m.upper(), p) for p, ops in spec["paths"].items() for m, op in ops.items() if "senses" in op.get("tags", [])}
    app = create_app(("app.api_senses.router",))
    served = {(m.upper(), p) for p, ops in app.openapi()["paths"].items() for m in ops}
    assert wanted - served == set()


@pytest.fixture
async def api(live_env):
    from app.main import create_app

    app = create_app(("app.api_senses.router",))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def _last(stream: str) -> str:
    entries = await bus.get_redis().xrevrange(stream, count=1)
    return entries[0][0] if entries else "0-0"


async def _since(stream: str, last: str):
    return [parse_envelope(f["data"]) for _, f in await bus.get_redis().xrange(stream, min=f"({last}")]


async def test_vehicle_registration_and_telemetry(api, live_env):
    r = await api.post("/api/vehicles", json={"label": "AP-16-TX-0001", "driver_name": "Driver", "driver_phone": "+919000000001",
                                              "driver_language": "te", "capacity_kg": 3000})
    assert r.status_code == 201, r.text
    vehicle, token = r.json()["vehicle"], r.json()["device_token"]
    assert vehicle["is_offline"] and vehicle["position"] is None
    assert await live_env.fetchval("SELECT device_token_hash FROM vehicles") != token  # only the hash is stored

    now = datetime.now(UTC)
    fixes = [{"lon": 80.62 + i * 0.001, "lat": 16.51, "recorded_at": (now - timedelta(seconds=30 - i * 10)).isoformat(),
              "speed_mps": 8.0, "heading_deg": 90, "accuracy_m": 5} for i in range(3)]
    last = await _last("vehicle.telemetry")
    bad = await api.post("/api/telemetry", json={"vehicle_id": vehicle["id"], "fixes": fixes}, headers={"X-Device-Token": "nope"})
    assert bad.status_code == 401
    ok = await api.post("/api/telemetry", json={"vehicle_id": vehicle["id"], "fixes": fixes}, headers={"X-Device-Token": token})
    assert ok.json() == {"accepted": 3, "duplicates": 0}
    again = await api.post("/api/telemetry", json={"vehicle_id": vehicle["id"], "fixes": fixes}, headers={"X-Device-Token": token})
    assert again.json() == {"accepted": 0, "duplicates": 3}  # offline queue resend
    events = await _since("vehicle.telemetry", last)
    assert [round(e.payload.position.lon, 3) for e in events] == [80.62, 80.621, 80.622]

    [v] = (await api.get("/api/vehicles")).json()
    assert v["position"]["coordinates"] == [80.622, 16.51] and not v["is_offline"]

    future = [{"lon": 80.6, "lat": 16.5, "recorded_at": (now + timedelta(hours=1)).isoformat()}]
    assert (await api.post("/api/telemetry", json={"vehicle_id": vehicle["id"], "fixes": future},
                           headers={"X-Device-Token": token})).status_code == 422


async def test_supplier_portal_flow(api, live_env):
    assert "supplier check-in" in (await api.get("/portal")).text
    fac = await live_env.fetchval(
        """INSERT INTO facilities (source, source_ref, kind, name, geom, fetched_at)
           VALUES ('osm', 'way/1', 'warehouse', 'Krishna Cold Storage', ST_SetSRID(ST_MakePoint(80.6, 16.5), 4326), now())
           RETURNING id""")
    found = (await api.get("/portal/api/facilities", params={"q": "krishna", "lon": 80.61, "lat": 16.5})).json()
    assert found[0]["id"] == str(fac) and 1000 < found[0]["distance_m"] < 1100
    reg = await api.post("/portal/api/register", json={"name": "Krishna Traders", "contact_whatsapp": "+919000000002",
                                                       "preferred_language": "te", "facility_id": str(fac)})
    assert reg.status_code == 201 and reg.json()["verified"] is False
    token = reg.json()["supplier_token"]

    last = await _last("supplier.stock.updated")
    lines = [{"item_code": "WATER_L", "quantity": 5000, "unit_weight_kg": 1}, {"item_code": "RICE_KG", "quantity": 800, "unit_weight_kg": 1}]
    r = await api.post("/portal/api/stock", json={"lines": lines}, headers={"X-Supplier-Token": token})
    assert r.status_code == 200, r.text
    assert {(x["item_code"], x["available"]) for x in r.json()} == {("WATER_L", 5000), ("RICE_KG", 800)}
    r2 = await api.post("/portal/api/stock", json={"lines": [lines[0] | {"quantity": 4200}]}, headers={"X-Supplier-Token": token})
    assert r2.status_code == 200
    events = await _since("supplier.stock.updated", last)
    assert [(e.payload.item_code, e.payload.quantity, e.payload.previous_quantity) for e in events] == [
        ("WATER_L", 5000, None), ("RICE_KG", 800, None), ("WATER_L", 4200, 5000)]

    assert (await api.post("/portal/api/stock", json={"lines": lines}, headers={"X-Supplier-Token": "x"})).status_code == 401
    dup = {"lines": [lines[0], lines[0]]}
    assert (await api.post("/portal/api/stock", json=dup, headers={"X-Supplier-Token": token})).status_code == 422
    gps = await api.post("/portal/api/register", json={"name": "Field Depot", "lon": 80.7, "lat": 16.6})
    assert gps.status_code == 201
    assert await live_env.fetchval("SELECT kind::text FROM facilities WHERE source = 'portal'") == "warehouse"
    both = await api.post("/portal/api/register", json={"name": "X", "facility_id": str(fac), "lon": 1, "lat": 1})
    assert both.status_code == 422


async def test_whatsapp_disabled_without_twilio(api, monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "")
    get_senses_settings.cache_clear()
    assert (await api.post("/portal/whatsapp", data={"From": "whatsapp:+1", "Body": "x"})).status_code == 503


def test_twilio_signature_scheme():
    params = {"From": "whatsapp:+919000000002", "Body": "WATER_L 10 1"}
    url = "https://example.org/portal/whatsapp"
    data = url + "".join(k + params[k] for k in sorted(params))
    sig = base64.b64encode(hmac.new(b"secret", data.encode(), hashlib.sha1).digest()).decode()
    assert twilio_signature_valid("secret", url, params, sig)
    assert not twilio_signature_valid("other", url, params, sig)


def test_whatsapp_stock_parsing():
    lines = parse_whatsapp_stock("water_l 5000 1; RICE_KG 800 1\nTARPAULIN 40 4.5")
    assert [(l.item_code.value, l.quantity) for l in lines] == [("WATER_L", 5000), ("RICE_KG", 800), ("TARPAULIN", 40)]
    for bad in ("", "WATER_L 5000", "GOLD 1 1"):
        with pytest.raises(ValueError):
            parse_whatsapp_stock(bad)


async def test_sse_streams_agent_events_and_resumes(live_env, monkeypatch):
    """Rows are written as A would write them; the stream must deliver them in order and resume by id."""
    from app.api_senses import stream as stream_mod

    monkeypatch.setattr(stream_mod, "TICK_S", 0.05)
    plan = await live_env.fetchval(
        "INSERT INTO plans (thread_id, trigger_kind, requested_by) VALUES ('t-sse', 'drill', 'test') RETURNING id")
    ids = [await live_env.fetchval(
        "INSERT INTO agent_events (plan_id, node, event_type, message) VALUES ($1, 'sentinel', 'decision', $2) RETURNING id",
        plan, f"msg {i}") for i in range(3)]

    class FakeRequest:
        def __init__(self, polls): self.polls = polls
        async def is_disconnected(self):
            self.polls -= 1
            return self.polls < 0

    chunks = [c async for c in stream_mod.event_stream(FakeRequest(3), plan, None)]
    agent = [c for c in chunks if "event: agent_event" in c]
    assert [f"id: {i}" in c for i, c in zip(ids, agent)] == [True] * 3
    resumed = [c async for c in stream_mod.event_stream(FakeRequest(2), plan, ids[0])]
    assert sum("event: agent_event" in c for c in resumed) == 2


async def test_sse_emits_plan_status_changes_once_each(live_env, monkeypatch):
    import json

    from app.api_senses import stream as stream_mod

    monkeypatch.setattr(stream_mod, "TICK_S", 0.05)
    monkeypatch.setattr(stream_mod, "PLAN_EVERY_S", 0.01)
    parent = await live_env.fetchval(
        "INSERT INTO plans (thread_id, trigger_kind, requested_by) VALUES ('t-p', 'drill', 'test') RETURNING id")
    child = await live_env.fetchval(
        """INSERT INTO plans (thread_id, trigger_kind, requested_by, parent_plan_id, status)
           VALUES ('t-c', 'replan', 'replanner', $1, 'planning') RETURNING id""", parent)

    class Request:
        polls = 4

        async def is_disconnected(self):
            Request.polls -= 1
            if Request.polls == 1:  # a status change during the connection
                await live_env.execute("UPDATE plans SET status = 'dispatched', updated_at = now() WHERE id = $1", child)
            return Request.polls < 0

    chunks = [c async for c in stream_mod.event_stream(Request(), None, None)]
    plans = [json.loads(c.split("data: ", 1)[1]) for c in chunks if "event: plan" in c]
    assert sorted(p["status"] for p in plans) == ["dispatched", "pending", "planning"]  # the unchanged parent only once
    assert {p["parent_plan_id"] for p in plans} == {None, str(parent)}


async def test_driver_app_is_served_without_path_traversal(api):
    assert "ReliefRoute Driver" in (await api.get("/driver/")).text
    assert (await api.get("/driver/sw.js")).headers["service-worker-allowed"] == "/driver/"
    for bad in ("/driver/../backend/.env", "/driver/%2e%2e/backend/.env", "/driver/README.md"):
        assert (await api.get(bad)).status_code == 404, bad
