"""Every live source misbehaving the way real sources do: timeouts, 429s, garbage bodies, XML
attacks, repeated alerts. Rows already stored must survive, and nothing may be invented."""

import httpx
import pytest

from app.core.resilience import CircuitBreaker, SourceUnavailable
from app.ingestion.base import FetchResult
from app.ingestion.runner import poll_once
from app.ingestion.sources.facilities import FacilitiesSource
from app.ingestion.sources.gdacs import GdacsSource
from app.ingestion.sources.open_meteo import OpenMeteoSource
from app.ingestion.sources.sachet import SachetSource, parse_cap
from app.ingestion.worker import RETRY_AFTER_FAILURE_S, next_delay_s

from ..conftest import NoKnownRows, fixture_text
from ..test_integration_store_api import FakeSource, real_alert  # noqa: F401

BBOX = (80.0, 16.0, 81.0, 17.0)


def client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def timeouts(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("no answer")


def rate_limited(request: httpx.Request) -> httpx.Response:
    return httpx.Response(429, headers={"Retry-After": "60"}, text="Too Many Requests")


def garbage(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="<html>maintenance page</html>")


def sources():
    return [
        SachetSource("https://sachet.example/rss", 300),
        GdacsSource("India", 7, 900),
        OpenMeteoSource(BBOX, 1.0, 3600),
    ]


@pytest.mark.parametrize("handler", [timeouts, rate_limited], ids=["timeout", "429"])
@pytest.mark.parametrize("index", [0, 1, 2], ids=["sachet", "gdacs", "open_meteo"])
async def test_source_outage_raises_a_handled_error_never_a_crash(index, handler, monkeypatch):
    source = sources()[index]
    source.breaker = CircuitBreaker(source.name, failure_threshold=1, reset_after_s=60)
    async with client(handler) as http:
        with pytest.raises((SourceUnavailable, httpx.HTTPError, ValueError)):
            await source.fetch(http, NoKnownRows())


@pytest.mark.parametrize("index", [0, 1, 2], ids=["sachet", "gdacs", "open_meteo"])
async def test_garbage_body_is_a_handled_error_not_fake_data(index):
    source = sources()[index]
    async with client(garbage) as http:
        try:
            result = await source.fetch(http, NoKnownRows())
        except (SourceUnavailable, httpx.HTTPError, ValueError):
            return
    assert result.hazards == []  # tolerated, but nothing was made up from it


async def test_every_overpass_mirror_rate_limited_falls_back_to_the_labelled_extract(tmp_path):
    extract = tmp_path / "facilities.geojsonseq"
    extract.write_text('\x1e{"type":"Feature","id":"n1","geometry":{"type":"Point","coordinates":[80.6,16.5]},'
                       '"properties":{"amenity":"hospital","name":"Govt General Hospital"}}\n', encoding="utf-8")
    (tmp_path / "facilities.timestamp").write_text("2026-10-29T21:21:26Z", encoding="utf-8")
    source = FacilitiesSource(["https://overpass-a.example", "https://overpass-b.example"], BBOX, extract, 86400)
    async with client(rate_limited) as http:
        result = await source.fetch(http, NoKnownRows())
    assert [f.name for f in result.facilities] == ["Govt General Hospital"]
    assert result.used_fallback and "extract" in result.used_fallback


async def test_overpass_down_with_no_extract_fails_loudly(tmp_path):
    source = FacilitiesSource(["https://overpass.example"], BBOX, tmp_path / "missing.geojsonseq", 86400)
    async with client(timeouts) as http:
        with pytest.raises((SourceUnavailable, FileNotFoundError, ValueError)):
            await source.fetch(http, NoKnownRows())


@pytest.mark.parametrize("xml", [
    "not xml at all",
    "<alert><info></info>",
    '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><alert>&b;</alert>',
], ids=["text", "truncated", "entity_bomb"])
def test_malformed_or_hostile_cap_is_rejected(xml):
    with pytest.raises(Exception) as caught:
        parse_cap(xml)
    assert not isinstance(caught.value, MemoryError)


def test_valid_cap_still_parses_after_the_hostile_cases():
    assert parse_cap(fixture_text("sachet_cap_lightning_ap.xml"))["event"] == "Lightning"


def test_failed_poll_retries_soon_but_a_good_one_waits_its_interval():
    class Slow:
        poll_s = 6 * 3600

    class Quick:
        poll_s = 30

    assert next_delay_s(Slow, ok=True) == 6 * 3600
    assert next_delay_s(Slow, ok=False) == RETRY_AFTER_FAILURE_S
    assert next_delay_s(Quick, ok=False) == 30


@pytest.mark.integration
async def test_duplicate_alert_changes_nothing_and_stored_data_survives_an_outage(live_env):
    pool = live_env
    async with httpx.AsyncClient() as http:
        first = await poll_once(FakeSource(FetchResult([real_alert()], [])), http, pool)
        repeat = await poll_once(FakeSource(FetchResult([real_alert()], [])), http, pool)
        down = await poll_once(FakeSource(error=SourceUnavailable("test_source", "HTTP 503")), http, pool)
    assert first.events == 1 and repeat.events == 0 and not down.ok
    assert await pool.fetchval("SELECT count(*) FROM hazards") == 1
    assert await pool.fetchval("SELECT version FROM hazards") == 1
    health = await pool.fetchrow("SELECT last_error FROM source_health WHERE source = 'test_source'")
    assert "HTTP 503" in health["last_error"]
