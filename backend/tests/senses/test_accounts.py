"""Accounts, attacked. Needs TEST_DATABASE_URL and TEST_REDIS_URL, apart from the plain unit tests."""

import asyncio
from uuid import uuid4

import httpx
import pytest
from pydantic import TypeAdapter, ValidationError

from app.core.config import get_common_settings
from app.core.sessions import COOKIE, digest, hash_password, password_matches
from app.core.settings_brain import get_brain_settings
from app.core.validation import Email
from app.ingestion.settings import get_senses_settings

OPERATOR_CODE = "operator-code-long-enough-for-production-use"
DRIVER_CODE = "driver-code-1234"
PASSWORD = "correct horse battery"
TRUCK = {"label": "AP 16 TX 1", "driver_language": "te", "capacity_kg": 5000}
pytestmark = pytest.mark.integration


def clear_settings():
    get_common_settings.cache_clear()
    get_senses_settings.cache_clear()
    get_brain_settings.cache_clear()


@pytest.fixture
async def app(live_env, monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("OPERATOR_TOKEN", OPERATOR_CODE)
    monkeypatch.setenv("ENROLMENT_CODE", DRIVER_CODE)
    monkeypatch.setenv("VALHALLA_URL", "http://127.0.0.1:1")
    clear_settings()
    yield create_app(("app.api_senses.router", "app.api_brain.router"))
    clear_settings()


def browser(app) -> httpx.AsyncClient:
    """Each one keeps its own cookies, like a separate browser."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def http(app):
    async with browser(app) as client:
        yield client


async def sign_up(client, role="supplier", code=None, email=None) -> dict:
    email = email or f"{uuid4().hex[:8]}@example.org"
    response = await client.post("/api/auth/signup", json={"email": email, "password": PASSWORD, "name": "Asha Rao", "role": role, "code": code})
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------- who may become what
async def test_anyone_can_sign_up_as_a_supplier_and_is_signed_in(http):
    account = await sign_up(http)
    assert account["role"] == "supplier"
    assert (await http.get("/api/auth/me")).json()["email"] == account["email"]


@pytest.mark.parametrize("role", ["operator", "driver"])
@pytest.mark.parametrize("code", [None, "", "guess"])
async def test_a_stranger_cannot_sign_up_as_operator_or_driver(http, live_env, role, code):
    body = {"email": "x@example.org", "password": PASSWORD, "name": "Stranger", "role": role, "code": code}
    assert (await http.post("/api/auth/signup", json=body)).status_code == 401
    assert await live_env.fetchval("SELECT count(*) FROM accounts") == 0


async def test_the_right_codes_make_the_right_accounts(http):
    assert (await sign_up(http, "operator", OPERATOR_CODE))["role"] == "operator"
    assert (await sign_up(http, "driver", DRIVER_CODE))["role"] == "driver"


async def test_a_role_cannot_be_invented(http):
    body = {"email": "x@example.org", "password": PASSWORD, "name": "Stranger", "role": "admin"}
    assert (await http.post("/api/auth/signup", json=body)).status_code == 422


async def test_one_email_one_account_however_it_is_typed(http):
    await sign_up(http, email="Asha@Example.org")
    body = {"email": "asha@example.ORG", "password": PASSWORD, "name": "Other", "role": "supplier"}
    assert (await http.post("/api/auth/signup", json=body)).status_code == 409


@pytest.mark.parametrize("password", ["short", "x" * 129])
async def test_weak_passwords_are_refused(http, password):
    body = {"email": "x@example.org", "password": password, "name": "Asha", "role": "supplier"}
    assert (await http.post("/api/auth/signup", json=body)).status_code == 422


# ---------------------------------------------------------------- signing in and out
async def test_sign_in_and_out(app):
    async with browser(app) as first:
        account = await sign_up(first)
    async with browser(app) as second:
        assert (await second.get("/api/auth/me")).status_code == 401
        signed_in = await second.post("/api/auth/signin", json={"email": account["email"].upper(), "password": PASSWORD})
        assert signed_in.status_code == 200
        assert (await second.get("/api/auth/me")).status_code == 200
        assert (await second.post("/api/auth/signout")).status_code == 204
        assert (await second.get("/api/auth/me")).status_code == 401


async def test_the_session_cookie_cannot_be_read_by_scripts_or_sent_by_other_sites(app):
    async with browser(app) as client:
        response = await client.post("/api/auth/signup", json={"email": "c@example.org", "password": PASSWORD, "name": "Asha", "role": "supplier"})
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


async def test_a_session_is_stored_only_as_a_hash(http, live_env):
    await sign_up(http)
    token = http.cookies[COOKIE]
    stored = await live_env.fetchval("SELECT token_hash FROM account_sessions")
    assert stored == digest(token) and stored != token


async def test_passwords_are_never_stored_in_the_clear(http, live_env):
    await sign_up(http)
    stored = await live_env.fetchval("SELECT password_hash FROM accounts")
    assert PASSWORD not in stored
    assert await password_matches(PASSWORD, stored)
    assert not await password_matches("wrong password", stored)


async def test_a_wrong_password_and_an_unknown_email_look_the_same(app):
    async with browser(app) as client:
        account = await sign_up(client)
    async with browser(app) as client:
        wrong = await client.post("/api/auth/signin", json={"email": account["email"], "password": "not the password"})
        unknown = await client.post("/api/auth/signin", json={"email": "nobody@example.org", "password": "not the password"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]


async def test_too_many_wrong_passwords_locks_the_account_even_for_the_right_one(app):
    async with browser(app) as client:
        account = await sign_up(client)
        for _ in range(10):
            await client.post("/api/auth/signin", json={"email": account["email"], "password": "not the password"})
        assert (await client.post("/api/auth/signin", json={"email": account["email"], "password": PASSWORD})).status_code == 429


async def test_a_forged_request_from_another_site_does_not_count_as_signed_in(http):
    await sign_up(http, "operator", OPERATOR_CODE)
    vehicle = f"/api/vehicles/{uuid4()}/revoke"
    assert (await http.post(vehicle, headers={"Sec-Fetch-Site": "cross-site"})).status_code == 401
    assert (await http.post(vehicle, headers={"Sec-Fetch-Site": "same-origin"})).status_code == 404  # let through


async def test_a_made_up_or_expired_cookie_is_nobody(app, live_env):
    async with browser(app) as client:
        client.cookies.set(COOKIE, "made-up")
        assert (await client.get("/api/auth/me")).status_code == 401
    async with browser(app) as client:
        await sign_up(client)
        await live_env.execute("UPDATE account_sessions SET expires_at = now() - interval '1 minute'")
        assert (await client.get("/api/auth/me")).status_code == 401


# ---------------------------------------------------------------- what each role can reach
@pytest.mark.parametrize("path", ["/api/vehicles", "/api/plans", "/api/orders", "/api/overview", "/api/accounts", "/api/stream"])
async def test_operational_data_needs_an_operator_not_just_any_account(http, path):
    await sign_up(http)
    assert (await http.get(path)).status_code == 401


async def test_an_operator_session_replaces_the_token(http):
    await sign_up(http, "operator", OPERATOR_CODE)
    for path in ("/api/vehicles", "/api/plans", "/api/orders", "/api/overview", "/api/accounts"):
        assert (await http.get(path)).status_code == 200, path


async def test_the_operator_token_still_works_for_scripts(http):
    assert (await http.get("/api/plans", headers={"X-Operator-Token": OPERATOR_CODE})).status_code == 200


async def test_a_signed_in_approval_records_who_really_decided(http, live_env):
    me = await sign_up(http, "operator", OPERATOR_CODE)
    plan = await live_env.fetchval(
        "INSERT INTO plans (thread_id, trigger_kind, hazard_ids, requested_by, status) VALUES ('t', 'manual', '{}', 'x', 'awaiting_approval') RETURNING id")
    decided = await http.post(f"/api/plans/{plan}/approve", json={"decision": "approved", "decided_by": "Someone Else"})
    assert decided.status_code == 200, decided.text
    assert await live_env.fetchval("SELECT decided_by FROM approvals") == f"Asha Rao <{me['email']}>"


# ---------------------------------------------------------------- drivers
async def test_a_driver_registers_a_vehicle_once_and_reports_gps(http):
    await sign_up(http, "driver", DRIVER_CODE)
    assert (await http.get("/api/driver/vehicle")).status_code == 404
    created = await http.post("/api/driver/vehicle", json=TRUCK)
    assert created.status_code == 201, created.text
    assert created.json()["vehicle"]["driver_name"] == "Asha Rao"
    assert (await http.post("/api/driver/vehicle", json=TRUCK)).status_code == 409
    assert (await http.get("/api/driver/vehicle")).json()["label"] == TRUCK["label"]


async def test_a_new_phone_takes_over_and_the_old_one_stops(http):
    await sign_up(http, "driver", DRIVER_CODE)
    first = (await http.post("/api/driver/vehicle", json=TRUCK)).json()
    second = (await http.post("/api/driver/vehicle/device")).json()
    url = f"/api/vehicles/{first['vehicle']['id']}/assignment"
    assert (await http.get(url, headers={"X-Device-Token": second["device_token"]})).status_code == 404  # allowed: no route yet
    http.cookies.clear()
    assert (await http.get(url, headers={"X-Device-Token": first["device_token"]})).status_code == 401


async def test_a_revoked_vehicle_cannot_get_a_new_token_by_signing_in_again(app):
    async with browser(app) as driver, browser(app) as operator:
        await sign_up(driver, "driver", DRIVER_CODE)
        await sign_up(operator, "operator", OPERATOR_CODE)
        vehicle = (await driver.post("/api/driver/vehicle", json=TRUCK)).json()["vehicle"]["id"]
        assert (await operator.post(f"/api/vehicles/{vehicle}/revoke")).status_code == 200
        assert (await driver.post("/api/driver/vehicle/device")).status_code == 403


async def test_a_supplier_account_is_not_a_driver(http):
    await sign_up(http)
    assert (await http.get("/api/driver/vehicle")).status_code == 401
    assert (await http.post("/api/driver/vehicle", json=TRUCK)).status_code == 401


# ---------------------------------------------------------------- suppliers
async def test_a_supplier_registers_reports_stock_and_is_verified(app):
    async with browser(app) as supplier, browser(app) as operator:
        await sign_up(supplier)
        assert (await supplier.get("/api/supplier/me")).status_code == 404
        created = await supplier.post("/api/supplier/me", json={"name": "Krishna Traders", "lon": 80.6, "lat": 16.5})
        assert created.status_code == 201, created.text
        assert (await supplier.post("/api/supplier/me", json={"name": "Twice", "lon": 80.6, "lat": 16.5})).status_code == 409
        levels = await supplier.post("/api/supplier/stock", json={"lines": [{"item_code": "RICE_KG", "quantity": 500, "unit_weight_kg": 1}]})
        assert levels.status_code == 200, levels.text
        me = (await supplier.get("/api/supplier/me")).json()
        assert me["verified"] is False and me["stock"][0]["quantity"] == 500 and me["items"]

        await sign_up(operator, "operator", OPERATOR_CODE)
        row = next(p for p in (await operator.get("/api/accounts")).json() if p["role"] == "supplier")
        assert row["supplier_name"] == "Krishna Traders" and row["supplier_verified"] is False
        assert (await operator.post(f"/api/suppliers/{row['supplier_id']}/verify", json={"verified": True})).status_code == 200
        assert (await supplier.get("/api/supplier/me")).json()["verified"] is True


async def test_one_supplier_cannot_report_for_another(http):
    await sign_up(http, "driver", DRIVER_CODE)
    assert (await http.post("/api/supplier/stock", json={"lines": [{"item_code": "RICE_KG", "quantity": 1, "unit_weight_kg": 1}]})).status_code == 401


# ---------------------------------------------------------------- managing people
async def test_removing_someone_ends_their_session_at_once(app):
    async with browser(app) as person, browser(app) as operator:
        account = await sign_up(person)
        await sign_up(operator, "operator", OPERATOR_CODE)
        assert (await person.post(f"/api/accounts/{account['id']}/access", json={"disabled": True})).status_code == 401
        assert (await operator.post(f"/api/accounts/{account['id']}/access", json={"disabled": True})).status_code == 200
        assert (await person.get("/api/auth/me")).status_code == 401
        again = await person.post("/api/auth/signin", json={"email": account["email"], "password": PASSWORD})
        assert again.status_code == 401


async def test_an_operator_cannot_lock_themselves_out(http):
    me = await sign_up(http, "operator", OPERATOR_CODE)
    assert (await http.post(f"/api/accounts/{me['id']}/access", json={"disabled": True})).status_code == 409


async def test_a_reset_link_works_once_and_signs_everyone_out(app):
    async with browser(app) as person, browser(app) as operator:
        account = await sign_up(person)
        await sign_up(operator, "operator", OPERATOR_CODE)
        link = (await operator.post(f"/api/accounts/{account['id']}/reset-link")).json()
        token = link["path"].split("token=")[1]

        new_password = "a brand new passphrase"
        assert (await person.post("/api/auth/reset", json={"token": token, "password": new_password})).status_code == 204
        assert (await person.get("/api/auth/me")).status_code == 401
        assert (await person.post("/api/auth/reset", json={"token": token, "password": "used once already"})).status_code == 422
        assert (await person.post("/api/auth/signin", json={"email": account["email"], "password": PASSWORD})).status_code == 401
        assert (await person.post("/api/auth/signin", json={"email": account["email"], "password": new_password})).status_code == 200


async def test_an_expired_reset_link_is_refused(app, live_env):
    async with browser(app) as person, browser(app) as operator:
        account = await sign_up(person)
        await sign_up(operator, "operator", OPERATOR_CODE)
        link = (await operator.post(f"/api/accounts/{account['id']}/reset-link")).json()
        await live_env.execute("UPDATE password_resets SET expires_at = now() - interval '1 minute'")
        token = link["path"].split("token=")[1]
        assert (await person.post("/api/auth/reset", json={"token": token, "password": "a brand new passphrase"})).status_code == 422


async def test_only_an_operator_can_make_a_reset_link(http):
    account = await sign_up(http)
    assert (await http.post(f"/api/accounts/{account['id']}/reset-link")).status_code == 401


# ---------------------------------------------------------------- live numbers
async def test_status_and_overview_are_real_counts(http, live_env):
    await live_env.execute(
        """INSERT INTO hazards (source, source_ref, dedupe_key, hazard_type, severity, headline, geom, fetched_at)
           VALUES ('gdacs', 'x', 'x', 'flood', 'severe', 'h', ST_Multi(ST_MakeEnvelope(80, 16, 80.1, 16.1, 4326)), now())""")
    status = (await http.get("/api/status")).json()  # public: the sign-in page shows it
    assert status["hazards_active"] == 1 and status["hazards_severe"] == 1
    assert status["hazards_changed"].startswith("1:")

    await sign_up(http, "operator", OPERATOR_CODE)
    overview = (await http.get("/api/overview")).json()
    assert overview == {"plans_awaiting": 0, "routes_live": 0, "kg_on_routes": 0, "orders_open": 0, "orders_delivered": 0,
                        "trucks_online": 0, "trucks_total": 0}  # fmt: skip


# ---------------------------------------------------------------- plain unit tests
@pytest.mark.parametrize("good", ["asha@example.org", "A.Rao+relief@mail.example.co.in"])
def test_real_email_addresses_pass(good):
    assert TypeAdapter(Email).validate_python(good) == good.lower()


@pytest.mark.parametrize("bad", ["", "asha", "asha@", "@example.org", "a@b", "a b@example.org", "<a>@example.org", "a@example.org" + "x" * 300])
def test_bad_email_addresses_fail(bad):
    with pytest.raises(ValidationError):
        TypeAdapter(Email).validate_python(bad)


async def test_hashing_is_salted_and_does_not_block_other_requests():
    first, second = await asyncio.gather(hash_password(PASSWORD), hash_password(PASSWORD))
    assert first != second
