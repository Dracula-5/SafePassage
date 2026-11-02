"""Populated places and administrative boundaries from the official OSM extract.

Built by infra/osm/extract_places_admin.sh. Populations come from OSM `population` tags (mostly
Census 2011); settlements without the tag are stored with population NULL so coverage can be
reported honestly. There is no live API for this data; the extract's date is the data age.
"""

from __future__ import annotations

import json
from pathlib import Path

import asyncpg
import httpx
from shapely import MultiPolygon, Point
from shapely.geometry import shape

from app.core.logging import get_logger
from app.core.resilience import CircuitBreaker, SourceUnavailable

from ..area_names import normalize
from ..base import FetchResult
from ..models import AdminArea, NormalizedPlace, to_multipolygon

log = get_logger(__name__)
OSM_TYPES = {"n": "node", "w": "way", "r": "relation", "a": "area"}


def _ref(feature: dict) -> str:
    fid = str(feature.get("id", ""))
    return f"{OSM_TYPES.get(fid[:1], 'unknown')}/{fid[1:]}"


def _features(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            yield json.loads(line.lstrip("\x1e"))


def parse_population(value: object) -> int | None:
    text = str(value or "").replace(",", "").strip()
    return int(text) if text.isdigit() else None


def parse_places(path: Path) -> list[NormalizedPlace]:
    places = []
    for f in _features(path):
        p = f.get("properties", {})
        if f["geometry"]["type"] != "Point" or not p.get("place"):
            continue
        population = parse_population(p.get("population"))
        source = p.get("source:population") or p.get("census:population") or p.get("population:date")
        places.append(NormalizedPlace(
            source="osm", source_ref=_ref(f), place_type=p["place"], name=p.get("name:en") or p.get("name"),
            geom=Point(f["geometry"]["coordinates"]), population=population,
            population_source=str(source).lower() if population is not None and source else None,
        ))  # fmt: skip
    return places


def parse_admin(path: Path) -> list[AdminArea]:
    areas = []
    for f in _features(path):
        p = f.get("properties", {})
        name = p.get("name:en") or p.get("name")
        level = str(p.get("admin_level", ""))
        if not name or not level.isdigit() or not normalize(name):
            continue
        try:
            geom = to_multipolygon(shape(f["geometry"]))
        except ValueError:
            continue
        areas.append(AdminArea(source_ref=_ref(f), admin_level=int(level), name=name, name_norm=normalize(name),
                               geom=geom if isinstance(geom, MultiPolygon) else MultiPolygon([geom])))  # fmt: skip
    return areas


class OsmExtractSource:
    name = "osm_extract"
    ttl_s = 30 * 24 * 3600  # the extract is refreshed by re-running the infra script

    def __init__(self, places_path: Path, admin_path: Path, poll_s: int):
        self.places_path = places_path
        self.admin_path = admin_path
        self.poll_s = poll_s
        self.breaker = CircuitBreaker("osm_extract")

    async def fetch(self, http: httpx.AsyncClient, conn: asyncpg.Pool) -> FetchResult:
        missing = [str(p) for p in (self.places_path, self.admin_path) if not p.exists()]
        if missing:
            raise SourceUnavailable(self.name, f"run infra/osm/extract_places_admin.sh; missing {missing}")
        places, areas = parse_places(self.places_path), parse_admin(self.admin_path)
        with_pop = sum(1 for p in places if p.population is not None)
        log.info("osm_extract_loaded", places=len(places), with_population=with_pop, admin_areas=len(areas))
        return FetchResult(hazards=[], facilities=[], places=places, admin_areas=areas)
