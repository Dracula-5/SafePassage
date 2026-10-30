"""Relief-relevant facilities from OpenStreetMap.

Primary: Overpass API (tries each mirror). Fallback: the facilities extract cut from the same
Geofabrik OSM file by infra/osm/extract_facilities.sh; used when every mirror fails, and
reported as a fallback in source_health. Only named features are kept.

OSM tag -> facility kind:
  amenity=hospital|clinic -> hospital (clinic kept in attributes.amenity)
  amenity=school -> school (Indian schools are the usual designated relief camps)
  amenity=shelter (not bus/picnic/gazebo types), amenity=refugee_site,
  social_facility=shelter, emergency=assembly_point -> shelter
  building=warehouse -> warehouse;  amenity=fuel -> fuel
"""

from __future__ import annotations

import json
from pathlib import Path

import asyncpg
import httpx
from reliefroute_contracts.enums import FacilityKind, FacilitySource
from shapely import Point
from shapely.geometry import shape

from app.core.logging import get_logger
from app.core.resilience import CircuitBreaker, SourceUnavailable, resilient_request

from ..base import FetchResult
from ..models import NormalizedFacility

log = get_logger(__name__)
NOT_RELIEF_SHELTERS = {"public_transport", "gazebo", "picnic_shelter", "parking", "pergola", "field_shelter",
                       "sun_shelter", "changing_rooms"}  # fmt: skip


def classify(tags: dict) -> FacilityKind | None:
    amenity = tags.get("amenity")
    if amenity in ("hospital", "clinic"):
        return FacilityKind.HOSPITAL
    if amenity == "school":
        return FacilityKind.SCHOOL
    if amenity == "fuel":
        return FacilityKind.FUEL
    if (amenity == "shelter" and tags.get("shelter_type") not in NOT_RELIEF_SHELTERS) or amenity == "refugee_site" \
            or tags.get("social_facility") == "shelter" or tags.get("emergency") == "assembly_point":
        return FacilityKind.SHELTER
    if tags.get("building") == "warehouse":
        return FacilityKind.WAREHOUSE
    return None


def _capacity(tags: dict) -> int | None:
    value = str(tags.get("capacity", "")).strip()
    return int(value) if value.isdigit() else None


def facility(osm_type: str, osm_id: int | str, tags: dict, point: Point) -> NormalizedFacility | None:
    kind = classify(tags)
    name = (tags.get("name:en") or tags.get("name") or "").strip()
    if kind is None or not name:
        return None
    keep = {k: tags[k] for k in ("amenity", "healthcare", "beds", "operator", "name:te", "addr:district",
                                 "shelter_type", "emergency") if k in tags}  # fmt: skip
    return NormalizedFacility(source=FacilitySource.OSM, source_ref=f"{osm_type}/{osm_id}", kind=kind, name=name,
                              geom=point, capacity_people=_capacity(tags), attributes=keep)


def overpass_query(bbox: tuple[float, float, float, float]) -> str:
    s, w, n, e = bbox[1], bbox[0], bbox[3], bbox[2]
    b = f"({s},{w},{n},{e})"
    return f"""[out:json][timeout:180];
(nwr["amenity"~"^(hospital|clinic|school|fuel|shelter|refugee_site)$"]["name"]{b};
 nwr["social_facility"="shelter"]["name"]{b};
 nwr["emergency"="assembly_point"]["name"]{b};
 nwr["building"="warehouse"]["name"]{b};);
out center tags;"""


def parse_overpass(payload: dict) -> list[NormalizedFacility]:
    out = []
    for el in payload.get("elements", []):
        lon, lat = (el.get("lon"), el.get("lat")) if el["type"] == "node" else \
            ((el.get("center") or {}).get("lon"), (el.get("center") or {}).get("lat"))
        if lon is None or lat is None:
            continue
        f = facility(el["type"], el["id"], el.get("tags", {}), Point(lon, lat))
        if f:
            out.append(f)
    return out


def parse_extract(path: Path) -> list[NormalizedFacility]:
    """GeoJSON sequence from `osmium export --add-unique-id=type_id` (feature ids like 'n123', 'w45')."""
    out = []
    with path.open() as fh:
        for line in fh:
            feature = json.loads(line.lstrip("\x1e"))
            fid = str(feature.get("id", ""))
            osm_type = {"n": "node", "w": "way", "r": "relation", "a": "area"}.get(fid[:1], "unknown")
            geom = shape(feature["geometry"])
            f = facility(osm_type, fid[1:], feature.get("properties", {}),
                         geom if isinstance(geom, Point) else geom.representative_point())
            if f:
                out.append(f)
    return out


class FacilitiesSource:
    name = "overpass"
    ttl_s = 3 * 24 * 3600

    def __init__(self, urls: list[str], bbox: tuple[float, float, float, float], extract: Path, poll_s: int):
        self.urls = urls
        self.bbox = bbox
        self.extract = extract
        self.poll_s = poll_s
        self.breaker = CircuitBreaker("overpass", failure_threshold=len(urls) * 2, reset_after_s=1800)

    async def fetch(self, http: httpx.AsyncClient, conn: asyncpg.Pool) -> FetchResult:
        query = overpass_query(self.bbox)
        errors = []
        for url in self.urls:
            try:
                resp = await resilient_request(http, "POST", url, self.breaker, data={"data": query},
                                               timeout_s=120, retries=0)  # fmt: skip
                if resp.status_code == 200:
                    found = parse_overpass(resp.json())
                    log.info("overpass_polled", url=url, facilities=len(found))
                    return FetchResult(hazards=[], facilities=found)
                errors.append(f"{url}: HTTP {resp.status_code}")
            except SourceUnavailable as exc:
                errors.append(f"{url}: {exc.reason}")
        if not self.extract.exists():
            raise SourceUnavailable(self.name, "; ".join(errors) + "; no fallback extract")
        found = parse_extract(self.extract)
        log.warning("overpass_unavailable_using_extract", errors=errors, facilities=len(found), path=str(self.extract))
        return FetchResult(hazards=[], facilities=found, used_fallback=f"osm_extract:{self.extract.name}")
