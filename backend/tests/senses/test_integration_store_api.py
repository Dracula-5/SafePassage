"""Store, runner and API against the real PostGIS + Redis (scratch database per test).

Hazard rows here are built in the test from the captured live fixtures, or drawn as drills.
"""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from reliefroute_contracts.events import parse_envelope
from shapely import box

from app.core import bus
from app.core.resilience import CircuitBreaker, SourceUnavailable
from app.ingestion.base import FetchResult
from app.ingestion.models import NormalizedHazard
from app.ingestion.runner import poll_once
from app.ingestion.sources.sachet import parse_cap, parse_polygon_file, to_hazard
from app.ingestion.store import expire_due_hazards, upsert_hazard

from .conftest import fixture_text

pytestmark = pytest.mark.integration


def real_alert(guid="g1", **overrides) -> NormalizedHazard:
    cap = parse_cap(fixture_text("sachet_cap_lightning_ap.xml")) | overrides
    return to_hazard(guid, cap, parse_polygon_file(fixture_text("sachet_polygon_ap.xml")))


async def test_upsert_versions_only_on_change(live_env):
    async with live_env.acquire() as conn:
        created = await upsert_hazard(conn, real_alert("g1"))
        assert created.change == "created" and created.version == 1 and created.area_km2 > 100
        assert await upsert_hazard(conn, real_alert("g2")) is None  # re-issued duplicate
        updated = await upsert_hazard(conn, real_alert("g3", severity="extreme"))
        assert updated.change == "updated" and updated.version == 2 and updated.severity == "extreme"
        cancelled = await upsert_hazard(conn, real_alert("g4", severity="extreme", msg_type="Cancel"))
        assert cancelled.change == "cancelled" and cancelled.version == 3
        assert await conn.fetchval("SELECT count(*) FROM hazards") == 1


async def test_expiry_sweep(live_env):
    past = datetime.now(UTC) - timedelta(minutes=1)
    async with live_env.acquire() as conn:
        await upsert_hazard(conn, real_alert("g1", expires=past))
        [event] = await expire_due_hazards(conn)
        assert event.change == "expired" and event.version == 2
        assert await expire_due_hazards(conn) == []


class FakeSource:
    """Test double for the runner: returns a prepared result or raises."""

    name, poll_s, ttl_s = "test_source", 60, 300

    def __init__(self, result=None, error=None):
        self.result, self.error = result, error
        self.breaker = CircuitBreaker("test_source")

    async def fetch(self, http, pool):
        if self.error:
            raise self.error
        return self.result


async def test_runner_publishes_events_and_records_health(live_env):
    redis = bus.get_redis()
    last = (await redis.xrevrange("hazard.updated", count=1) or [("0-0", {})])[0][0]
    async with httpx.AsyncClient() as http:
        ok = await poll_once(FakeSource(FetchResult([real_alert()], [])), http, live_env)
        again = await poll_once(FakeSource(FetchResult([real_alert("other-guid")], [])), http, live_env)
        failed = await poll_once(FakeSource(error=SourceUnavailable("test_source", "HTTP 503")), http, live_env)
    assert (ok.events, again.events, failed.ok) == (1, 0, False)
    entries = await redis.xrange("hazard.updated", min=f"({last}")
    envelopes = [parse_envelope(f["data"]) for _, f in entries]
    assert [e.payload.change for e in envelopes] == ["created"]
    health = await live_env.fetchrow("SELECT * FROM v_source_freshness WHERE source = 'test_source'")
    assert health["last_error"] == "SourceUnavailable: test_source: HTTP 503" and health["last_success_at"] is not None
    assert health["is_stale"] is False  # failed just now, but the last success is within the TTL


@pytest.fixture
async def api(live_env):
    from app.main import create_app

    app = create_app(("app.api_senses.router",))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


DRILL = {"type": "Polygon", "coordinates": [[[80.40, 16.40], [80.60, 16.40], [80.60, 16.60], [80.40, 16.60], [80.40, 16.40]]]}


async def test_drill_lifecycle(api, live_env):
    redis = bus.get_redis()
    last = (await redis.xrevrange("hazard.updated", count=1) or [("0-0", {})])[0][0]
    r = await api.post("/api/drill", json={"geometry": DRILL, "hazard_type": "flood", "severity": "severe",
                                           "label": "Krishna embankment breach", "ttl_minutes": 30})
    assert r.status_code == 201, r.text
    feature = r.json()
    assert feature["properties"]["is_drill"] and feature["properties"]["headline"] == "DRILL: Krishna embankment breach"
    assert feature["properties"]["source_ref"].startswith("drill:")

    listed = (await api.get("/api/hazards")).json()["features"]
    assert [f["id"] for f in listed] == [feature["id"]]
    assert (await api.get("/api/hazards", params={"include_drill": "false"})).json()["features"] == []
    assert (await api.get("/api/hazards", params={"bbox": "70,10,71,11"})).json()["features"] == []

    assert (await api.delete(f"/api/drill/{feature['id']}")).status_code == 204
    assert (await api.get("/api/hazards")).json()["features"] == []
    assert (await api.delete(f"/api/drill/{feature['id']}")).status_code == 204  # already cancelled: no-op
    changes = [parse_envelope(f["data"]).payload for _, f in await redis.xrange("hazard.updated", min=f"({last}")]
    assert [(c.change, c.is_drill, c.version) for c in changes] == [("created", True, 1), ("cancelled", True, 2)]


@pytest.mark.parametrize("geometry,status", [
    ({"type": "Polygon", "coordinates": [[[80, 16], [81, 17], [81, 16], [80, 17], [80, 16]]]}, 422),  # bow-tie
    ({"type": "Point", "coordinates": [80, 16]}, 422),
    ({"type": "Polygon", "coordinates": [[[60, 0], [100, 0], [100, 30], [60, 30], [60, 0]]]}, 422),   # too large
])
async def test_drill_rejects_bad_geometry(api, geometry, status):
    r = await api.post("/api/drill", json={"geometry": geometry, "hazard_type": "flood", "severity": "severe", "label": "x"})
    assert r.status_code == status, r.text


async def test_cancel_unknown_or_real_hazard_is_404(api, live_env):
    async with live_env.acquire() as conn:
        real = await upsert_hazard(conn, real_alert())
    assert (await api.delete(f"/api/drill/{real.hazard_id}")).status_code == 404


async def test_facilities_vehicles_and_health(api, live_env):
    await live_env.execute(
        """INSERT INTO facilities (source, source_ref, kind, name, geom, fetched_at) VALUES
           ('osm', 'node/1', 'hospital', 'H1', ST_SetSRID(ST_MakePoint(80.5, 16.5), 4326), now()),
           ('osm', 'node/2', 'school', 'S1', ST_SetSRID(ST_MakePoint(81.5, 17.5), 4326), now())""")
    hospitals = (await api.get("/api/facilities", params={"kind": "hospital"})).json()["features"]
    assert [f["properties"]["name"] for f in hospitals] == ["H1"]
    assert len((await api.get("/api/facilities", params={"bbox": "81,17,82,18"})).json()["features"]) == 1
    assert (await api.get("/api/facilities", params={"kind": "castle"})).status_code == 422
    assert (await api.get("/api/vehicles")).json() == []
    assert (await api.get("/api/sources/health")).json() == []
