"""Attacks on the running app: each test does what an outsider or a broken client would do.

Needs TEST_DATABASE_URL and TEST_REDIS_URL, apart from the tests marked as plain unit tests.
"""

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError

from app.api_senses import stream
from app.core import auth, bus, security
from app.core.config import CommonSettings, get_common_settings
from app.core.logging import scrub
from app.core.security import BodyLimit, Limit, RateLimit, SecurityHeaders, production_problems
from app.core.settings_brain import get_brain_settings
from app.core.validation import Name, Text
from app.ingestion.settings import get_senses_settings
from app.ingestion.worker import prune_telemetry
from app.suppliers.router import twilio_signature_valid

TOKEN = "operator-token-long-enough-for-production-use"
CODE = "enrol-code-1234"
OPERATOR = {"X-Operator-Token": TOKEN}
ENROL = {"X-Enrolment-Code": CODE}
TRUCK = {"label": "AP 16 TX 1", "driver_language": "te", "capacity_kg": 5000}
FIX = {"lon": 80.6, "lat": 16.5}


def clear_settings():
    get_common_settings.cache_clear()
    get_senses_settings.cache_clear()
    get_brain_settings.cache_clear()


def fixes():
    return [FIX | {"recorded_at": datetime.now(UTC).isoformat()}]


@pytest.fixture
async def client(live_env, monkeypatch):
    """The whole app with every protection switched on, as in production."""
    from app.main import create_app

    monkeypatch.setenv("OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("ENROLMENT_CODE", CODE)
    monkeypatch.setenv("VALHALLA_URL", "http://127.0.0.1:1")
    clear_settings()
    app = create_app(("app.api_senses.router", "app.api_brain.router"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        yield http
    clear_settings()


async def register(client, label="AP 16 TX 1") -> tuple[str, str]:
    response = await client.post("/api/vehicles", json=TRUCK | {"label": label}, headers=ENROL)
    assert response.status_code == 201, response.text
    return response.json()["vehicle"]["id"], response.json()["device_token"]


# ---------------------------------------------------------------- who may join and who may look
@pytest.mark.integration
async def test_a_stranger_cannot_add_a_truck_to_the_fleet(client, live_env):
    assert (await client.post("/api/vehicles", json=TRUCK)).status_code == 401
    assert (await client.post("/api/vehicles", json=TRUCK, headers={"X-Enrolment-Code": "guess"})).status_code == 401
    assert await live_env.fetchval("SELECT count(*) FROM vehicles") == 0
    await register(client)
    assert await live_env.fetchval("SELECT count(*) FROM vehicles") == 1


@pytest.mark.integration
@pytest.mark.parametrize("path", ["/api/vehicles", "/api/plans", "/api/orders", "/api/brain/health", f"/api/plans/{uuid4()}"])
async def test_operational_data_needs_the_operator_token(client, path):
    assert (await client.get(path)).status_code == 401
    assert (await client.get(path, headers={"X-Operator-Token": "wrong"})).status_code == 401
    assert (await client.get(path, headers=OPERATOR)).status_code in (200, 404)


@pytest.mark.integration
@pytest.mark.parametrize("path", ["/health", "/api/hazards", "/api/facilities", "/api/sources/health", "/portal", "/driver/"])
async def test_public_information_stays_public(client, path):
    assert (await client.get(path)).status_code == 200


@pytest.mark.integration
async def test_a_route_is_only_visible_to_its_own_vehicle_or_an_operator(client):
    mine, my_token = await register(client, "MINE")
    other, other_token = await register(client, "OTHER")
    url = f"/api/vehicles/{mine}/assignment"
    assert (await client.get(url)).status_code == 401
    assert (await client.get(url, headers={"X-Device-Token": other_token})).status_code == 401
    assert (await client.get(url, headers={"X-Device-Token": "made-up"})).status_code == 401
    assert (await client.get(url, headers={"X-Device-Token": my_token})).status_code == 404  # allowed: nothing assigned
    assert (await client.get(url, headers=OPERATOR)).status_code == 404


@pytest.mark.integration
async def test_a_revoked_phone_is_cut_off_and_never_planned_for(client, live_env):
    vehicle, token = await register(client)
    device = {"X-Device-Token": token}
    sent = await client.post("/api/telemetry", json={"vehicle_id": vehicle, "fixes": fixes()}, headers=device)
    assert sent.status_code == 202

    assert (await client.post(f"/api/vehicles/{vehicle}/revoke")).status_code == 401
    assert (await client.post(f"/api/vehicles/{vehicle}/revoke", headers=OPERATOR)).status_code == 200
    assert (await client.post(f"/api/vehicles/{uuid4()}/revoke", headers=OPERATOR)).status_code == 404

    sent = await client.post("/api/telemetry", json={"vehicle_id": vehicle, "fixes": fixes()}, headers=device)
    assert sent.status_code == 401
    assert (await client.get(f"/api/vehicles/{vehicle}/assignment", headers=device)).status_code == 401
    assert await live_env.fetchval("SELECT status::text FROM vehicles WHERE id = $1", vehicle) == "maintenance"


@pytest.mark.integration
async def test_only_an_operator_decides_who_is_a_verified_supplier(client, live_env):
    reg = await client.post("/portal/api/register", json={"name": "Krishna Traders", "lon": 80.6, "lat": 16.5})
    supplier, token = reg.json()["supplier_id"], reg.json()["supplier_token"]
    url = f"/api/suppliers/{supplier}/verify"
    assert (await client.post(url, json={"verified": True})).status_code == 401
    assert (await client.post(url, json={"verified": True}, headers=OPERATOR)).status_code == 200
    assert await live_env.fetchval("SELECT verified FROM suppliers WHERE id = $1", supplier) is True

    assert (await client.post(url, json={"verified": False}, headers=OPERATOR)).status_code == 200
    stock = {"lines": [{"item_code": "WATER_L", "quantity": 1, "unit_weight_kg": 1}]}
    assert (await client.post("/portal/api/stock", json=stock, headers={"X-Supplier-Token": token})).status_code == 401


# ---------------------------------------------------------------- the live stream
@pytest.mark.integration
async def test_the_stream_refuses_without_a_ticket(client):
    assert (await client.get("/api/stream")).status_code == 401
    assert (await client.get("/api/stream?ticket=123.abc")).status_code == 401
    assert (await client.post("/api/stream/ticket")).status_code == 401
    assert (await client.post("/api/stream/ticket", headers=OPERATOR)).json()["expires_in"] == auth.STREAM_TICKET_TTL_S


@pytest.mark.integration
async def test_stream_connections_are_capped(client, monkeypatch):
    monkeypatch.setattr(stream, "open_streams", stream.MAX_STREAMS)
    assert (await client.get("/api/stream", headers=OPERATOR)).status_code == 503


async def test_a_closed_stream_gives_its_slot_back(monkeypatch):
    async def one_chunk(*_):
        yield "data"

    monkeypatch.setattr(stream, "_events", one_chunk)
    monkeypatch.setattr(stream, "open_streams", 1)
    assert [chunk async for chunk in stream.event_stream(None, None, None)] == ["data"]
    assert stream.open_streams == 0


@pytest.mark.parametrize("given", [None, "", "abc", "٣٤", "-1", "9" * 30, str(2**63)])
def test_a_junk_last_event_id_starts_fresh_instead_of_crashing_the_query(given):
    assert stream._resume_from(given) is None


def test_stream_tickets_expire_and_cannot_be_forged(monkeypatch):
    key = "k" * 40
    monkeypatch.setenv("OPERATOR_TOKEN", key)
    get_common_settings.cache_clear()
    good = auth.stream_ticket()["ticket"]
    get_common_settings.cache_clear()
    expires, _, signature = good.partition(".")
    assert auth.valid_stream_ticket(key, good)
    assert not auth.valid_stream_ticket("other-key", good)
    assert not auth.valid_stream_ticket(key, f"{int(expires) + 999}.{signature}")  # extending it breaks the signature
    assert not auth.valid_stream_ticket(key, f"1.{auth._sign_ticket(key, 1)}")     # genuinely signed, long expired
    for junk in (None, "", ".", "x.y", "9" * 500 + ".z", "٣.z"):
        assert not auth.valid_stream_ticket(key, junk)


# ---------------------------------------------------------------- input that breaks things
@pytest.mark.integration
@pytest.mark.parametrize("path,body", [
    ("/api/vehicles", TRUCK | {"label": "AP\x00 16"}),
    ("/api/vehicles", TRUCK | {"driver_name": "Ravi‮ivaR"}),
    ("/portal/api/register", {"name": "Shop\x00", "lon": 80.6, "lat": 16.5}),
    ("/portal/api/register", {"name": "<img src=x onerror=alert(1)>", "lon": 80.6, "lat": 16.5}),
    ("/portal/api/register", {"name": "Depot", "lon": 10.0, "lat": 10.0}),
    ("/api/plans/trigger", {"requested_by": "x\x00"}),
    ("/api/drill", {"geometry": {"type": "Polygon", "coordinates": 5}, "hazard_type": "flood", "severity": "severe", "label": "x"}),
])  # fmt: skip
async def test_hostile_input_is_a_clean_refusal(client, path, body):
    response = await client.post(path, json=body, headers=OPERATOR | ENROL)
    assert response.status_code == 422, response.text


@pytest.mark.integration
async def test_a_non_ascii_secret_is_a_refusal_not_a_crash(client):
    for headers in ({"X-Operator-Token": "tök".encode("latin-1")}, {"X-Enrolment-Code": "tök".encode("latin-1")}):
        assert (await client.post("/api/vehicles/" + str(uuid4()) + "/revoke", headers=headers)).status_code == 401
        assert (await client.post("/api/vehicles", json=TRUCK, headers=headers)).status_code == 401


@pytest.mark.integration
async def test_infinity_cannot_get_into_telemetry(client):
    vehicle, token = await register(client)
    body = '{"vehicle_id": "%s", "fixes": [{"lon": 80.6, "lat": 16.5, "recorded_at": "%s", "accuracy_m": Infinity}]}' % (
        vehicle, datetime.now(UTC).isoformat())
    response = await client.post("/api/telemetry", content=body, headers={"X-Device-Token": token, "Content-Type": "application/json"})
    assert response.status_code == 422


@pytest.mark.integration
async def test_a_percent_sign_is_searched_for_not_treated_as_a_wildcard(client, live_env):
    await live_env.execute(
        """INSERT INTO facilities (source, source_ref, kind, name, geom, fetched_at)
           VALUES ('osm', 'a', 'godown', 'Godown 100% full', ST_SetSRID(ST_MakePoint(80.6, 16.5), 4326), now())""")
    assert [r["name"] for r in (await client.get("/portal/api/facilities", params={"q": "0%"})).json()] == ["Godown 100% full"]
    assert (await client.get("/portal/api/facilities", params={"q": "%%"})).json() == []


@pytest.mark.integration
async def test_an_oversized_body_is_stopped_at_the_door(client):
    big = b"x" * 2_000_000
    declared = await client.post("/api/telemetry", content=big, headers={"X-Device-Token": "x"})
    assert declared.status_code == 413

    async def chunks():  # sends no Content-Length, so only counting can catch it
        for _ in range(4):
            yield b"x" * 500_000

    undeclared = await client.post("/api/telemetry", content=chunks(), headers={"X-Device-Token": "x"})
    assert undeclared.status_code == 413


# ---------------------------------------------------------------- WhatsApp check-in
def signed(url: str, params: dict[str, str], auth_token: str) -> str:
    data = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()).decode()


@pytest.mark.integration
async def test_whatsapp_checkin_works_end_to_end_and_refuses_forgeries(client, live_env, monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "twilio-secret")
    get_senses_settings.cache_clear()
    await client.post("/portal/api/register", json={"name": "Krishna Traders", "contact_whatsapp": "+919000000002",
                                                    "lon": 80.6, "lat": 16.5})  # fmt: skip
    params = {"From": "whatsapp:+919000000002", "Body": "WATER_L 500 1"}
    url = "http://test/portal/whatsapp"
    form = {"Content-Type": "application/x-www-form-urlencoded"}

    forged = await client.post(url, content=urlencode(params), headers=form | {"X-Twilio-Signature": "AAAA"})
    assert forged.status_code == 403
    odd = await client.post(url, content=urlencode(params), headers=form | {"X-Twilio-Signature": "ö".encode("latin-1")})
    assert odd.status_code == 403
    assert await live_env.fetchval("SELECT count(*) FROM stock") == 0

    real = await client.post(url, content=urlencode(params), headers=form | {"X-Twilio-Signature": signed(url, params, "twilio-secret")})
    assert real.status_code == 200, real.text
    assert "WATER_L 500" in real.text
    assert await live_env.fetchval("SELECT quantity FROM stock") == 500


def test_signature_check_accepts_only_the_exact_message():
    params = {"Body": "x", "From": "y"}
    good = signed("https://a/b", params, "t")
    assert twilio_signature_valid("t", "https://a/b", params, good)
    assert not twilio_signature_valid("t", "https://a/b", params | {"Body": "z"}, good)
    assert not twilio_signature_valid("t", "https://a/b", params, "ö")


# ---------------------------------------------------------------- headers and limits
@pytest.mark.integration
async def test_every_response_carries_the_security_headers(client):
    headers = (await client.get("/api/hazards")).headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["cache-control"] == "no-store"
    assert "default-src 'self'" in headers["content-security-policy"]
    assert "strict-transport-security" not in headers  # development is plain http


@pytest.mark.integration
async def test_the_portal_script_runs_by_nonce_and_nothing_else_can(client):
    page = await client.get("/portal")
    csp = page.headers["content-security-policy"]
    nonce = csp.split("'nonce-")[1].split("'")[0]
    assert f'<script nonce="{nonce}">' in page.text
    assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
    assert nonce not in (await client.get("/portal")).text


def limited_app(limit: Limit) -> FastAPI:
    app = FastAPI()

    @app.get("/x")
    async def x() -> PlainTextResponse:
        return PlainTextResponse("ok")

    security.LIMITS[("GET", "/x")] = limit
    app.add_middleware(RateLimit, enabled=True)
    return app


def client_at(app: FastAPI, address: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(address, 1)), base_url="http://test")


@pytest.mark.integration
async def test_rate_limit_stops_one_address_and_leaves_others_alone(live_env, monkeypatch):
    monkeypatch.setitem(security.LIMITS, ("GET", "/x"), Limit(2, 60))
    app = limited_app(Limit(2, 60))
    noisy = f"203.0.113.{uuid4().int % 250}"
    async with client_at(app, noisy) as http:
        assert [(await http.get("/x")).status_code for _ in range(3)] == [200, 200, 429]
        refused = await http.get("/x")
        assert refused.headers["content-type"] == "application/problem+json"
        assert 0 < int(refused.headers["retry-after"]) <= 60
    async with client_at(app, f"198.51.100.{uuid4().int % 250}") as http:
        assert (await http.get("/x")).status_code == 200


async def test_rate_limit_still_works_with_redis_down(monkeypatch):
    monkeypatch.setitem(security.LIMITS, ("GET", "/x"), Limit(2, 60))
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    get_common_settings.cache_clear()
    await bus.close_redis()
    async with client_at(limited_app(Limit(2, 60)), "203.0.113.9") as http:
        assert [(await http.get("/x")).status_code for _ in range(3)] == [200, 200, 429]
    await bus.close_redis()
    get_common_settings.cache_clear()


def test_paths_cannot_mint_rate_limit_keys():
    assert ("GET", "/api/vehicles/some-uuid/assignment") not in security.LIMITS  # all of these share one bucket


async def test_body_limit_and_headers_leave_a_streaming_response_alone():
    """BaseHTTPMiddleware would buffer this; /api/stream depends on chunks arriving one by one."""
    seen = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        for chunk in (b"a", b"b"):
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b""})

    wrapped = SecurityHeaders(BodyLimit(app, limit=10), production=True)

    async def send(message):
        seen.append(message)

    await wrapped({"type": "http", "path": "/api/stream", "method": "GET", "headers": []}, None, send)
    assert [m.get("body") for m in seen[1:]] == [b"a", b"b", b""]
    assert (b"strict-transport-security", security.HSTS.encode()) in seen[0]["headers"]


# ---------------------------------------------------------------- start-up refusal
GOOD = dict(app_env="production", operator_token="t" * 40, enrolment_code="c" * 16, cors_origins=["https://relief.example"],
            database_url="postgresql://relief:long-random@db/relief", redis_url="redis://:long-random@redis/0",
            rate_limit_enabled=True)  # fmt: skip


def settings(**changes) -> CommonSettings:
    return CommonSettings(**GOOD | changes)


def test_good_production_settings_pass():
    assert production_problems(settings()) == []


@pytest.mark.parametrize("changes,word", [
    ({"operator_token": None}, "OPERATOR_TOKEN"),
    ({"operator_token": "short"}, "OPERATOR_TOKEN"),
    ({"enrolment_code": ""}, "ENROLMENT_CODE"),
    ({"cors_origins": ["*"]}, "CORS_ORIGINS"),
    ({"cors_origins": ["http://relief.example"]}, "CORS_ORIGINS"),
    ({"database_url": "postgresql://reliefroute:reliefroute@db/reliefroute"}, "DATABASE_URL"),
    ({"redis_url": "redis://redis:6379/0"}, "REDIS_URL"),
    ({"rate_limit_enabled": False}, "RATE_LIMIT_ENABLED"),
])  # fmt: skip
def test_each_unsafe_production_setting_is_named(changes, word):
    assert any(word in found for found in production_problems(settings(**changes)))


def test_every_problem_is_reported_at_once():
    found = production_problems(settings(operator_token=None, enrolment_code=None, cors_origins=["*"]))
    assert len(found) == 3


def test_development_is_left_alone():
    assert production_problems(settings(app_env="development", operator_token=None)) == []


def test_a_typo_in_app_env_stops_the_start():
    with pytest.raises(ValidationError):
        settings(app_env="prod")


def apply_production_env(monkeypatch, **changes):
    for key, value in (GOOD | changes).items():
        monkeypatch.setenv(key.upper(), json.dumps(value) if isinstance(value, list) else str(value))
    get_common_settings.cache_clear()


def test_production_refuses_to_start_with_unsafe_settings(monkeypatch):
    from app.main import create_app

    apply_production_env(monkeypatch, operator_token="short")
    with pytest.raises(RuntimeError, match="OPERATOR_TOKEN"):
        create_app(())
    get_common_settings.cache_clear()


async def test_production_hides_the_api_docs_and_turns_on_hsts(monkeypatch):
    from app.main import create_app

    apply_production_env(monkeypatch)
    app = create_app(())
    get_common_settings.cache_clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as http:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert (await http.get(path)).status_code == 404
        assert "strict-transport-security" in (await http.get("/nothing")).headers


async def test_an_unexpected_error_tells_the_client_nothing(monkeypatch):
    from app.main import create_app

    app = create_app(())

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("table secret_things on host db-internal")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.get("/boom")
    assert response.status_code == 500
    assert "secret_things" not in response.text
    assert response.json()["title"] == "Internal error"


# ---------------------------------------------------------------- secrets, text and retention
@pytest.mark.parametrize("text,secret", [
    ("connect to postgresql://relief:hunter2@db:5432/relief failed", "hunter2"),
    ("redis://:hunter2@redis:6379/0 refused", "hunter2"),
    ("GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/abcd1234/VIIRS/76,12,84,20/1 failed", "abcd1234"),
    ("OPERATOR_TOKEN=hunter2 was rejected", "hunter2"),
    ('{"api_key": "hunter2"}', "hunter2"),
])  # fmt: skip
def test_secrets_are_scrubbed_from_error_text(text, secret):
    assert secret not in scrub(text)


def test_ordinary_error_text_survives_scrubbing():
    text = "ConnectError: All connection attempts failed at sachet.ndma.gov.in"
    assert scrub(text) == text


def checked(annotation, value):
    from pydantic import TypeAdapter

    return TypeAdapter(annotation).validate_python(value)


@pytest.mark.parametrize("bad", ["a\x00b", "line\nbreak", "‮evil", "<b>", "a>b", "   "])
def test_names_refuse_what_would_mislead_or_break(bad):
    with pytest.raises(ValidationError):
        checked(Name, bad)


@pytest.mark.parametrize("good", ["Krishna Traders & Sons", "AP 16 TX 1234", "రవి శ్రీనివాస్", "श्री गणेश", "O'Brien"])
def test_real_names_pass(good):
    assert checked(Name, good) == good


def test_zero_width_joiners_stay_because_indian_scripts_need_them():
    assert checked(Name, "క్‍ష") == "క్‍ష"


def test_notes_keep_their_line_breaks_but_not_control_bytes():
    assert checked(Text, "first\nsecond") == "first\nsecond"
    with pytest.raises(ValidationError):
        checked(Text, "bad\x07bell")


@pytest.mark.integration
async def test_old_telemetry_is_pruned_and_recent_telemetry_kept(live_env):
    vehicle = await live_env.fetchval(
        "INSERT INTO vehicles (label, driver_language, capacity_kg) VALUES ('T', 'en', 100) RETURNING id")
    now = datetime.now(UTC)
    for age in (timedelta(days=40), timedelta(days=1)):
        await live_env.execute(
            """INSERT INTO telemetry (vehicle_id, geom, recorded_at)
               VALUES ($1, ST_SetSRID(ST_MakePoint(80.6, 16.5), 4326), $2)""", vehicle, now - age)
    assert await prune_telemetry(30) == 1
    assert await live_env.fetchval("SELECT count(*) FROM telemetry") == 1


@pytest.mark.integration
async def test_a_silent_worker_shows_up_as_stale(client):
    await bus.get_redis().delete("heartbeat:brain", "heartbeat:ingestion")
    rows = {r["source"]: r for r in (await client.get("/api/sources/health")).json()}
    assert rows["brain_worker"]["is_stale"] is True
    assert "not running" in rows["brain_worker"]["last_error"]

    await bus.get_redis().set("heartbeat:brain", datetime.now(UTC).isoformat(), ex=5)
    rows = {r["source"]: r for r in (await client.get("/api/sources/health")).json()}
    assert rows["brain_worker"]["is_stale"] is False
    await bus.get_redis().delete("heartbeat:brain")
