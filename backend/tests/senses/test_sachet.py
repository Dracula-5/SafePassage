import httpx
import pytest
from shapely.ops import unary_union

from app.core.resilience import SourceUnavailable
from app.ingestion.sources.sachet import (
    SachetSource, classify_event, parse_cap, parse_polygon_file, parse_polygon_text, to_hazard,
)

from .conftest import NoKnownRows, fixture_text


def test_parse_real_cap_picks_english_block():
    cap = parse_cap(fixture_text("sachet_cap_lightning_ap.xml"))
    assert cap["event"] == "Lightning" and cap["severity"] == "severe"
    assert cap["headline"].startswith("There is a possibility of lightning")
    assert "kkd-sankhavaram" in cap["area_desc"]
    assert cap["polygon_url"].endswith("identifier=1793373193098008")
    assert cap["expires"] > cap["effective"]


def test_parse_real_polygon_file():
    polys = parse_polygon_file(fixture_text("sachet_polygon_ap.xml"))
    assert len(polys) == 8
    lon, lat = polys[0].exterior.coords[0]
    assert 76 < lon < 85 and 12 < lat < 20  # CAP is lat,lon; we store lon,lat


def test_hazard_from_real_alert():
    cap = parse_cap(fixture_text("sachet_cap_lightning_ap.xml"))
    polys = parse_polygon_file(fixture_text("sachet_polygon_ap.xml"))
    h = to_hazard("1793373193098008", cap, polys)
    assert h.hazard_type == "storm" and h.severity == "severe" and h.status == "active"
    assert h.source_ref == "1793373193098008"
    # 8 mandal rings that share borders merge into their union; no area is lost
    assert h.geom.area == pytest.approx(unary_union(polys).area, rel=1e-9)


def test_reissued_alert_dedupes_but_cancel_changes_content():
    cap = parse_cap(fixture_text("sachet_cap_lightning_ap.xml"))
    polys = parse_polygon_file(fixture_text("sachet_polygon_ap.xml"))
    a, b = to_hazard("guid-1", cap, polys), to_hazard("guid-2", cap, polys)
    assert a.dedupe_key == b.dedupe_key and a.content_hash() == b.content_hash()
    cancelled = to_hazard("guid-3", cap | {"msg_type": "Cancel"}, polys)
    assert cancelled.dedupe_key == a.dedupe_key and cancelled.status == "cancelled"
    assert cancelled.content_hash() != a.content_hash()


@pytest.mark.parametrize("event,expected", [
    ("Lightning", "storm"), ("Heavy Rain", "heavy_rain"), ("Flood Situation", "flood"),
    ("Cyclonic Storm", "cyclone"), ("Heat Wave", "heatwave"), ("Landslide", "landslide"), ("Dust", "other"),
])
def test_classify_event(event, expected):
    assert classify_event(event) == expected


@pytest.mark.parametrize("text", ["", "17.3,82.4 17.4,82.5", "a,b c,d e,f g,h", "1,2,3 4,5,6 7,8,9 1,2,3"])
def test_malformed_polygon_text_is_rejected(text):
    assert parse_polygon_text(text) is None


def _transport(polygon_status=200, rss_status=200):
    def handler(request: httpx.Request):
        url = str(request.url)
        if url.endswith(".xml"):
            return httpx.Response(rss_status, text=fixture_text("sachet_rss.xml"))
        if "FetchPolygonXMLFile" in url:
            return httpx.Response(polygon_status, text=fixture_text("sachet_polygon_ap.xml") if polygon_status == 200 else "Error Code: 403")
        return httpx.Response(200, text=fixture_text("sachet_cap_lightning_ap.xml"))
    return httpx.MockTransport(handler)


async def test_fetch_is_bounded_and_dedupes():
    source = SachetSource("https://sachet.example/rss.xml", poll_s=60, max_new_per_poll=5, pause_s=0)
    async with httpx.AsyncClient(transport=_transport()) as http:
        result = await source.fetch(http, NoKnownRows())
    # 5 fetched; every CAP fixture is the same alert, so all share one dedupe key
    assert len(result.hazards) == 5 and len({h.dedupe_key for h in result.hazards}) == 1


async def test_rate_limited_polygons_mark_source_degraded():
    source = SachetSource("https://sachet.example/rss.xml", poll_s=60, max_new_per_poll=3, pause_s=0)
    async with httpx.AsyncClient(transport=_transport(polygon_status=403)) as http:
        with pytest.raises(SourceUnavailable, match="all 3 alert downloads failed.*HTTP 403"):
            await source.fetch(http, NoKnownRows())


async def test_rss_down_raises_source_unavailable():
    source = SachetSource("https://sachet.example/rss.xml", poll_s=60, pause_s=0)
    async with httpx.AsyncClient(transport=_transport(rss_status=404)) as http:
        with pytest.raises(SourceUnavailable):
            await source.fetch(http, NoKnownRows())
