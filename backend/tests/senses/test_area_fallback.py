"""SACHET fallback: CAP areaDesc names -> OSM admin boundaries, and the places loader."""

import json

import httpx
import pytest
from shapely import box

from app.ingestion import area_names
from app.ingestion.models import AdminArea
from app.ingestion.sources.osm_extract import parse_places, parse_population
from app.ingestion.sources.sachet import AREA_FALLBACK, SachetSource
from app.ingestion.store import upsert_admin_areas

from .conftest import fixture_text


@pytest.mark.parametrize("name,norm", [
    ("Gollaprolu Mandal", "gollaprolu"), ("gollaprolu mandals", "gollaprolu"), ("East Godavari District", "east godavari"),
    ("Adilabad Urban Mandal", "adilabad urban"), ("Kothapalle", "kothapalle")])
def test_normalize(name, norm):
    assert area_names.normalize(name) == norm


def test_split_real_area_desc():
    names, level = area_names.split_area_desc("kkd-sankhavaram, kkd-gollaprolu, kkd-kothapalle, pln-gurazala mandals ")
    assert names == ["sankhavaram", "gollaprolu", "kothapalle", "gurazala"] and level == 6
    names, level = area_names.split_area_desc("Srikakulam, Vizianagaram & Visakhapatnam")
    assert names == ["srikakulam", "vizianagaram", "visakhapatnam"] and level is None


@pytest.mark.parametrize("value,expected", [("1,234", 1234), ("850", 850), ("approx 900", None), (None, None)])
def test_parse_population(value, expected):
    assert parse_population(value) == expected


def test_parse_places(tmp_path):
    path = tmp_path / "p.geojsonseq"
    rows = [{"type": "Feature", "id": "n1", "geometry": {"type": "Point", "coordinates": [80.1, 16.1]},
             "properties": {"place": "village", "name": "V1", "population": "2,345", "source:population": "Census2011"}},
            {"type": "Feature", "id": "n2", "geometry": {"type": "Point", "coordinates": [80.2, 16.2]},
             "properties": {"place": "hamlet", "name": "H1"}}]
    path.write_text("".join("\x1e" + json.dumps(r) + "\n" for r in rows))
    v, h = parse_places(path)
    assert (v.population, v.population_source, v.source_ref) == (2345, "census2011", "node/1")
    assert h.population is None and h.population_source is None


def _area(ref, level, name, geom):
    from shapely import MultiPolygon
    return AdminArea(source_ref=ref, admin_level=level, name=name, name_norm=area_names.normalize(name),
                     geom=MultiPolygon([geom]))


@pytest.mark.integration
async def test_resolve_unique_ambiguous_and_state_filter(live_env):
    async with live_env.acquire() as conn:
        await upsert_admin_areas(conn, [
            _area("r/1", 4, "Andhra Pradesh", box(80, 16, 82, 18)),
            _area("r/2", 4, "Telangana", box(78, 16, 80, 18)),
            _area("r/10", 6, "Gollaprolu Mandal", box(81.0, 17.0, 81.2, 17.2)),
            _area("r/11", 6, "Sankhavaram Mandal", box(81.2, 17.0, 81.4, 17.2)),
            _area("r/12", 6, "Kothapalle Mandal", box(80.5, 16.5, 80.6, 16.6)),     # in AP
            _area("r/13", 6, "Kothapalle Mandal", box(79.0, 16.5, 79.1, 16.6)),     # same name in Telangana
            _area("r/14", 6, "Kothapalle Mandal", box(81.5, 16.5, 81.6, 16.6)),     # and again in AP -> ambiguous
        ])
        m = await area_names.resolve(conn, "kkd-sankhavaram, kkd-gollaprolu, kkd-kothapalle, pln-gurazala mandals",
                                     "Andhra-Pradesh-SDMA")
    assert m.matched == ["sankhavaram", "gollaprolu"]
    assert m.ambiguous == ["kothapalle"] and m.unmatched == ["gurazala"]
    assert m.geom.area == pytest.approx(0.08)


@pytest.mark.integration
async def test_sachet_403_falls_back_to_area_names(live_env):
    async with live_env.acquire() as conn:
        await upsert_admin_areas(conn, [
            _area("r/1", 4, "Andhra Pradesh", box(80, 16, 83, 18)),
            _area("r/10", 6, "Gollaprolu Mandal", box(82.0, 17.0, 82.2, 17.2)),
            _area("r/11", 6, "Sankhavaram Mandal", box(82.2, 17.0, 82.4, 17.2)),
            _area("r/12", 6, "Kothapalle Mandal", box(82.4, 17.0, 82.6, 17.2)),
            _area("r/13", 6, "Gurazala Mandal", box(81.0, 16.5, 81.2, 16.7))])
    polygon_calls = []

    def handler(request: httpx.Request):
        url = str(request.url)
        if url.endswith(".xml"):
            return httpx.Response(200, text=fixture_text("sachet_rss.xml"))
        if "FetchPolygonXMLFile" in url:
            polygon_calls.append(url)
            return httpx.Response(403, text="Error Code: 403")
        return httpx.Response(200, text=fixture_text("sachet_cap_lightning_ap.xml"))

    source = SachetSource("https://sachet.example/rss.xml", poll_s=60, max_new_per_poll=4, pause_s=0, concurrency=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await source.fetch(http, live_env)
    assert len(polygon_calls) == 1  # first 403 starts the cooldown; no more hammering
    assert result.used_fallback == AREA_FALLBACK and len(result.hazards) == 4
    h = result.hazards[0]
    assert h.raw["geometry_source"] == AREA_FALLBACK
    assert "Matched: sankhavaram, gollaprolu, kothapalle, gurazala" in h.description
    assert h.geom.area == pytest.approx(0.04 * 3 + 0.04)
