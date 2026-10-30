"""A's REST endpoints on a scratch database (graph execution itself is covered in test_integration_graph)."""

import os

import httpx
import pytest

from app.core import bus, db
from app.core.config import get_common_settings
from app.core.migrate import apply_all
from app.core.settings_brain import get_brain_settings

from .test_integration_db import scratch_db  # noqa: F401 (fixture)

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(scratch_db, monkeypatch):  # noqa: F811
    if not os.environ.get("TEST_REDIS_URL"):
        pytest.skip("TEST_REDIS_URL not set")
    await apply_all(scratch_db)
    url = f"{os.environ['TEST_DATABASE_URL'].rpartition('/')[0]}/{scratch_db._params.database}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("REDIS_URL", os.environ["TEST_REDIS_URL"])
    monkeypatch.setenv("VALHALLA_URL", os.environ.get("VALHALLA_URL", "http://127.0.0.1:1"))
    get_common_settings.cache_clear(), get_brain_settings.cache_clear()
    await db.close_pool(), await bus.close_redis()
    from app.main import create_app

    app = create_app(("app.api_brain.router",))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, scratch_db
    await db.close_pool(), await bus.close_redis()
    get_common_settings.cache_clear(), get_brain_settings.cache_clear()


async def test_trigger_is_idempotent_and_emits_plan_requested(api):
    client, conn = api
    hazard = await conn.fetchval(
        """INSERT INTO hazards (source, source_ref, dedupe_key, hazard_type, severity, headline, geom, fetched_at)
           VALUES ('gdacs', 'x', 'x', 'flood', 'severe', 'h', ST_Multi(ST_MakeEnvelope(80, 16, 80.1, 16.1, 4326)), now())
           RETURNING id""")
    redis = bus.get_redis()
    last = (await redis.xrevrange("plan.requested", count=1) or [("0-0", {})])[0][0]
    body = {"hazard_ids": [str(hazard)], "requested_by": "officer"}
    a = await client.post("/api/plans/trigger", json=body, headers={"Idempotency-Key": "k-1"})
    b = await client.post("/api/plans/trigger", json=body, headers={"Idempotency-Key": "k-1"})
    assert a.status_code == b.status_code == 202 and a.json()["id"] == b.json()["id"] and a.json()["status"] == "pending"
    assert len(await redis.xrange("plan.requested", min=f"({last}")) == 1
    listed = (await client.get("/api/plans")).json()
    assert [p["id"] for p in listed] == [a.json()["id"]]
    detail = (await client.get(f"/api/plans/{a.json()['id']}")).json()
    assert detail["routes"] == [] and detail["approval"] is None


async def test_approval_rules_and_problem_errors(api):
    client, conn = api
    plan = await conn.fetchval(
        "INSERT INTO plans (thread_id, trigger_kind, requested_by, status) VALUES ('t', 'manual', 'x', 'planning') RETURNING id")
    body = {"decision": "approved", "decided_by": "officer"}
    early = await client.post(f"/api/plans/{plan}/approve", json=body)
    assert early.status_code == 409 and early.headers["content-type"] == "application/problem+json"
    await conn.execute("UPDATE plans SET status = 'awaiting_approval' WHERE id = $1", plan)
    redis = bus.get_redis()
    last = (await redis.xrevrange("plan.approved", count=1) or [("0-0", {})])[0][0]
    ok = await client.post(f"/api/plans/{plan}/approve", json=body)
    assert ok.status_code == 200 and ok.json()["decision"] == "approved"
    assert len(await redis.xrange("plan.approved", min=f"({last}")) == 1
    again = await client.post(f"/api/plans/{plan}/approve", json=body | {"decision": "rejected"})
    assert again.status_code == 409 and "already has a decision" in again.json()["detail"]
    assert await conn.fetchval("SELECT message FROM agent_events WHERE event_type = 'approval_decided'") == "APPROVED by officer"
    missing = await client.get("/api/plans/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404 and missing.json()["title"] == "Request error"
    assert (await client.get("/api/vehicles/00000000-0000-0000-0000-000000000000/assignment")).status_code == 404
    assert (await client.get("/api/orders")).json() == []
