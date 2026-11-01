"""The database and Redis go away. The API must stay up, say so on /health, answer with a plain error that names no
host or password, and never sign anyone in or hand out data it could not read. conftest points DATABASE_URL at a closed port."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_common_settings
from app.main import create_app

LEAKS = ("127.0.0.1", "unused", "asyncpg", "Traceback", "password", "ConnectionRefused", "postgres")


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused:never-shown@127.0.0.1:1/unused")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    get_common_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client
    get_common_settings.cache_clear()


def test_health_reports_both_outages_and_the_api_stays_up(api):
    response = api.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "checks": {"postgres": "error", "redis": "error"}}
    assert api.get("/health").status_code == 503  # still answering: nothing crashed


@pytest.mark.parametrize("path", ["/api/hazards", "/api/facilities", "/api/sources/health", "/api/status"])
def test_public_reads_fail_plainly_without_leaking_where_the_database_is(api, path):
    response = api.get(path)
    assert response.status_code >= 500
    body = response.text
    assert not any(leak.lower() in body.lower() for leak in LEAKS), body
    assert "never-shown" not in body


def test_nobody_is_signed_in_while_the_database_is_down(api):
    response = api.post("/api/auth/signin", json={"email": "someone@example.org", "password": "a long enough passphrase"})
    assert response.status_code >= 500
    assert "rr_session" not in response.cookies
    assert "never-shown" not in response.text


def test_a_made_up_session_cookie_is_never_accepted_without_the_database(api):
    api.cookies.set("rr_session", "x" * 43)
    for path in ("/api/plans", "/api/vehicles", "/api/accounts", "/api/auth/me"):
        assert api.get(path).status_code != 200, path


def test_signup_does_not_pretend_to_succeed(api):
    response = api.post("/api/auth/signup", json={"email": "new@example.org", "password": "a long enough passphrase", "name": "New Person", "role": "supplier"})
    assert response.status_code >= 500
    assert "rr_session" not in response.cookies
