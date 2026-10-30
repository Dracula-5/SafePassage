"""GDACS (Global Disaster Alert and Coordination System) events affecting the country.

Event list -> per event, the published geometry. GDACS gives impact polygons labelled by
alert level (Poly_Red / Poly_Orange / Poly_Green for cyclone wind zones, flood areas, ...).
One hazard per (event, level). Events with only a point and no polygon are skipped: an
extent is never invented.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
from reliefroute_contracts.enums import HazardSeverity, HazardSource, HazardStatus, HazardType
from shapely.geometry import shape
from shapely.ops import unary_union

from app.core.logging import get_logger
from app.core.resilience import CircuitBreaker, SourceUnavailable, resilient_get

from ..base import FetchResult
from ..models import NormalizedHazard

log = get_logger(__name__)
EVENTS_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"

TYPES = {"TC": HazardType.CYCLONE, "FL": HazardType.FLOOD, "EQ": HazardType.EARTHQUAKE,
         "WF": HazardType.WILDFIRE, "TS": HazardType.TSUNAMI, "DR": HazardType.OTHER, "VO": HazardType.OTHER}  # fmt: skip
LEVELS = {"red": HazardSeverity.EXTREME, "orange": HazardSeverity.SEVERE, "green": HazardSeverity.MODERATE}
# GDACS leaves `iscurrent=true` for days after the last advisory. An event counts as over this
# long after its last advisory (`todate`); the expiry sweep then closes it.
ADVISORY_GRACE = timedelta(hours=24)


def _level(label: str) -> str | None:
    label = label.lower()
    return next((lvl for lvl in LEVELS if lvl in label), None)


def _dt(text: str | None) -> datetime | None:
    if not text:
        return None
    value = datetime.fromisoformat(text)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _expiry(props: dict, current: bool) -> datetime | None:
    last_advisory = _dt(props.get("todate"))
    if last_advisory is None:
        return None
    return last_advisory + ADVISORY_GRACE if current else last_advisory


def event_hazards(event: dict, geometry: dict) -> list[NormalizedHazard]:
    props = event["properties"]
    by_level: dict[str, list] = {}
    for feature in geometry.get("features", []):
        if feature["geometry"]["type"] not in ("Polygon", "MultiPolygon"):
            continue
        label = str(feature["properties"].get("Class") or feature["properties"].get("polygonlabel") or "")
        level = _level(label) or str(props.get("alertlevel", "")).lower()
        if level in LEVELS:
            by_level.setdefault(level, []).append(shape(feature["geometry"]))
    current = str(props.get("iscurrent", "true")).lower() == "true"
    hazards = []
    for level, geoms in by_level.items():
        hazards.append(NormalizedHazard(
            source=HazardSource.GDACS,
            source_ref=f"{props['eventtype']}:{props['eventid']}:{props.get('episodeid')}:{level}",
            dedupe_key=f"gdacs:{props['eventtype']}:{props['eventid']}:{level}",
            hazard_type=TYPES.get(props["eventtype"], HazardType.OTHER), severity=LEVELS[level],
            status=HazardStatus.ACTIVE if current else HazardStatus.EXPIRED,
            headline=f"{props.get('name') or props.get('eventname') or props['eventtype']} ({level} zone)",
            description=props.get("description") or props.get("htmldescription"),
            area_desc=props.get("country"), geom=unary_union(geoms),
            effective_at=_dt(props.get("fromdate")), expires_at=_expiry(props, current),
            issued_at=_dt(props.get("datemodified")),
            raw={k: props.get(k) for k in ("eventtype", "eventid", "episodeid", "alertlevel", "alertscore", "name",
                                            "fromdate", "todate", "iscurrent", "severitydata", "url")},
        ))  # fmt: skip
    return hazards


class GdacsSource:
    name = "gdacs"
    ttl_s = 3600

    def __init__(self, country: str, lookback_days: int, poll_s: int):
        self.country = country
        self.lookback_days = lookback_days
        self.poll_s = poll_s
        self.breaker = CircuitBreaker("gdacs", failure_threshold=3, reset_after_s=300)

    async def fetch(self, http: httpx.AsyncClient, conn: asyncpg.Pool) -> FetchResult:
        today = datetime.now(UTC).date()
        params = {"country": self.country, "fromdate": str(today - timedelta(days=self.lookback_days)),
                  "todate": str(today + timedelta(days=1))}  # fmt: skip
        resp = await resilient_get(http, EVENTS_URL, self.breaker, params=params, timeout_s=30)
        if resp.status_code == 204 or not resp.content:
            return FetchResult(hazards=[], facilities=[])  # GDACS answers 204 when there are no events
        if resp.status_code != 200:
            raise SourceUnavailable(self.name, f"HTTP {resp.status_code}")
        hazards, skipped = [], 0
        for event in resp.json().get("features", []):
            geom_url = (event["properties"].get("url") or {}).get("geometry")
            if not geom_url:
                skipped += 1
                continue
            geo = await resilient_get(http, geom_url, self.breaker, timeout_s=30)
            if geo.status_code != 200:
                skipped += 1
                log.warning("gdacs_geometry_failed", gdacs_event=event["properties"].get("eventid"), status=geo.status_code)
                continue
            found = event_hazards(event, geo.json())
            if not found:
                log.info("gdacs_event_without_polygon", gdacs_event=event["properties"].get("eventid"))
            hazards.extend(found)
        log.info("gdacs_polled", events=len(resp.json().get("features", [])), hazards=len(hazards), skipped=skipped)
        return FetchResult(hazards=hazards, facilities=[], skipped=skipped)
