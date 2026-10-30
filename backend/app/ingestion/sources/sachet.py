"""NDMA SACHET: national CAP 1.2 alerts (IMD, CWC, state SDMAs).

Flow per poll: RSS (last ~100 alerts) -> for alerts not seen before, the CAP XML and, when the
CAP references one, the SACHET polygon file (`<polygon>lat,lon ...</polygon>`, several per
alert). Alerts without any polygon are skipped: we never invent an extent from area names.
Duplicates: the same alert re-issued under a new identifier (same sender, event, area and
validity) collapses onto one hazard via dedupe_key.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime

import asyncpg
import httpx
from defusedxml import ElementTree as SafeET
from reliefroute_contracts.enums import HazardSeverity, HazardSource, HazardStatus, HazardType
from shapely import Polygon
from shapely.ops import unary_union

from app.core.logging import get_logger
from app.core.resilience import CircuitBreaker, SourceUnavailable, resilient_get

from .. import area_names
from ..base import FetchResult
from ..models import NormalizedHazard

log = get_logger(__name__)
CAP_NS = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}
CAP_URL = "https://sachet.ndma.gov.in/cap_public_website/FetchXMLFile?identifier={}"
AREA_FALLBACK = "areaDesc_osm_admin"

# Keyword -> hazard type, checked in order against the CAP <event> text.
EVENT_TYPES: list[tuple[tuple[str, ...], HazardType]] = [
    (("cyclone", "cyclonic", "depression"), HazardType.CYCLONE),
    (("flood", "inundation", "river"), HazardType.FLOOD),
    (("heavy rain", "rainfall", "rain"), HazardType.HEAVY_RAIN),
    (("thunder", "lightning", "squall", "storm", "gusty", "hail", "wind"), HazardType.STORM),
    (("landslide", "mudslide", "avalanche"), HazardType.LANDSLIDE),
    (("heat", "hot"), HazardType.HEATWAVE),
    (("fire",), HazardType.WILDFIRE),
    (("tsunami",), HazardType.TSUNAMI),
    (("earthquake",), HazardType.EARTHQUAKE),
]


def classify_event(event: str) -> HazardType:
    text = event.lower()
    for keywords, hazard_type in EVENT_TYPES:
        if any(k in text for k in keywords):
            return hazard_type
    return HazardType.OTHER


def parse_polygon_text(text: str) -> Polygon | None:
    """CAP polygon text: whitespace-separated 'lat,lon' pairs."""
    try:
        pts = [tuple(float(v) for v in pair.split(",")) for pair in text.split()]
    except ValueError:
        return None
    if len(pts) < 4 or any(len(p) != 2 for p in pts):
        return None
    return Polygon([(lon, lat) for lat, lon in pts])


def parse_polygon_file(xml_text: str) -> list[Polygon]:
    root = SafeET.fromstring(xml_text)
    polys = [parse_polygon_text(el.text or "") for el in root.iter("polygon")]
    return [p for p in polys if p is not None]


def _dt(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def parse_cap(xml_text: str) -> dict:
    """Pick the English <info> block (first block otherwise) and return the fields we use."""
    root = SafeET.fromstring(xml_text)
    infos = root.findall("cap:info", CAP_NS)
    if not infos:
        raise ValueError("CAP alert has no <info>")
    info = next((i for i in infos if (i.findtext("cap:language", "", CAP_NS) or "").lower().startswith("en")), infos[0])
    area = info.find("cap:area", CAP_NS)
    params = {p.findtext("cap:valueName", "", CAP_NS): p.findtext("cap:value", "", CAP_NS)
              for p in info.findall("cap:parameter", CAP_NS)}  # fmt: skip
    inline = [parse_polygon_text(el.text or "") for el in (area.findall("cap:polygon", CAP_NS) if area is not None else [])]
    return {
        "identifier": root.findtext("cap:identifier", "", CAP_NS),
        "sender": root.findtext("cap:sender", "", CAP_NS),
        "sent": _dt(root.findtext("cap:sent", None, CAP_NS)),
        "msg_type": root.findtext("cap:msgType", "Alert", CAP_NS),
        "references": root.findtext("cap:references", "", CAP_NS),
        "event": info.findtext("cap:event", "", CAP_NS),
        "severity": (info.findtext("cap:severity", "Unknown", CAP_NS) or "Unknown").lower(),
        "urgency": info.findtext("cap:urgency", "", CAP_NS),
        "certainty": info.findtext("cap:certainty", "", CAP_NS),
        "headline": (info.findtext("cap:headline", "", CAP_NS) or "").strip(),
        "description": (info.findtext("cap:description", "", CAP_NS) or "").strip() or None,
        "effective": _dt(info.findtext("cap:effective", None, CAP_NS)),
        "expires": _dt(info.findtext("cap:expires", None, CAP_NS)),
        "area_desc": (area.findtext("cap:areaDesc", "", CAP_NS) or "").strip() if area is not None else "",
        "polygon_url": params.get("Polygon URL"),
        "inline_polygons": [p for p in inline if p is not None],
    }


def to_hazard(
    guid: str, cap: dict, polygons: list[Polygon], geometry_source: str = "sachet_polygon",
    area_match: area_names.AreaMatch | None = None,
) -> NormalizedHazard:
    status = HazardStatus.CANCELLED if cap["msg_type"] == "Cancel" else HazardStatus.ACTIVE
    severity = cap["severity"] if cap["severity"] in HazardSeverity._value2member_map_ else "unknown"
    # Same sender + event + area + validity window = the same real-world alert, whatever its id.
    fingerprint = "|".join([cap["sender"], cap["event"], cap["area_desc"], str(cap["effective"]), str(cap["expires"])])
    return NormalizedHazard(
        source=HazardSource.SACHET, source_ref=guid,
        dedupe_key="sachet:" + hashlib.sha256(fingerprint.encode()).hexdigest()[:32],
        hazard_type=classify_event(cap["event"]), severity=severity, status=status,
        headline=cap["headline"] or cap["event"] or "SACHET alert",
        description=cap["description"] if geometry_source != AREA_FALLBACK else
        f"{cap['description'] or ''} [Area placed from the alert's area names via OSM boundaries; "
        f"SACHET polygon unavailable. Matched: {', '.join(area_match.matched)}"
        f"{'; not matched: ' + ', '.join(area_match.unmatched + area_match.ambiguous) if area_match.unmatched or area_match.ambiguous else ''}]".strip(),
        area_desc=cap["area_desc"], geom=unary_union(polygons),  # adjacent areas share borders
        effective_at=cap["effective"], expires_at=cap["expires"], issued_at=cap["sent"],
        raw={k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in cap.items()
             if k not in {"inline_polygons"}} | {"guid": guid, "rss_feed": "rss_india", "geometry_source": geometry_source},
    )  # fmt: skip


class SachetSource:
    name = "sachet"
    ttl_s = 600

    # SACHET rate-limits polygon downloads per IP (HTTP 403 after a burst), so new alerts are
    # fetched slowly and in bounded batches; the rest are picked up on the next polls.
    def __init__(self, rss_url: str, poll_s: int, concurrency: int = 2, max_new_per_poll: int = 25,
                 pause_s: float = 0.5, polygon_cooldown_s: float = 1800):
        self.rss_url = rss_url
        self.poll_s = poll_s
        self.max_new_per_poll = max_new_per_poll
        self.pause_s = pause_s
        self.polygon_cooldown_s = polygon_cooldown_s
        self._polygon_blocked_until = 0.0
        self.breaker = CircuitBreaker("sachet", failure_threshold=3, reset_after_s=900)
        self._sem = asyncio.Semaphore(concurrency)

    async def _download_polygons(self, http: httpx.AsyncClient, guid: str, url: str) -> list[Polygon] | None:
        """Polygons from SACHET, or None if SACHET refused (rate limit) or is in cooldown."""
        if time.monotonic() < self._polygon_blocked_until:
            return None
        await asyncio.sleep(self.pause_s)
        resp = await resilient_get(http, url, self.breaker, timeout_s=30)
        if resp.status_code == 403:
            self._polygon_blocked_until = time.monotonic() + self.polygon_cooldown_s
            log.warning("sachet_polygons_rate_limited", guid=guid, cooldown_s=self.polygon_cooldown_s)
            return None
        if resp.status_code != 200:
            raise ValueError(f"polygon {guid}: HTTP {resp.status_code}")
        return parse_polygon_file(resp.text)

    async def _alert(self, http: httpx.AsyncClient, conn: asyncpg.Pool, guid: str) -> NormalizedHazard | None:
        async with self._sem:
            cap_resp = await resilient_get(http, CAP_URL.format(guid), self.breaker, timeout_s=20)
            if cap_resp.status_code != 200:
                raise ValueError(f"CAP {guid}: HTTP {cap_resp.status_code}")
            cap = parse_cap(cap_resp.text)
            polygons: list[Polygon] | None = list(cap["inline_polygons"])
            if not polygons and cap["polygon_url"]:
                polygons = await self._download_polygons(http, guid, cap["polygon_url"])
            await asyncio.sleep(self.pause_s)
        if polygons:
            return to_hazard(guid, cap, polygons)
        # Fallback: official boundaries of the areas the alert names.
        match = await area_names.resolve(conn, cap["area_desc"], cap["sender"])
        if match.geom is not None:
            log.info("sachet_area_fallback", guid=guid, matched=match.matched, unmatched=match.unmatched,
                     ambiguous=match.ambiguous)  # fmt: skip
            return to_hazard(guid, cap, list(match.geom.geoms), geometry_source=AREA_FALLBACK, area_match=match)
        # Not placeable without guessing: SACHET gave no polygon and no area name matched uniquely.
        # This is a content limitation, not a source failure, so it does not count against health.
        log.info("sachet_alert_unplaceable", guid=guid, alert_event=cap["event"], area=cap["area_desc"][:80],
                 sachet_refused=polygons is None, unmatched=match.unmatched, ambiguous=match.ambiguous)
        return None

    async def fetch(self, http: httpx.AsyncClient, conn: asyncpg.Pool) -> FetchResult:
        rss = await resilient_get(http, self.rss_url, self.breaker, timeout_s=20)
        if rss.status_code != 200:
            raise SourceUnavailable(self.name, f"RSS HTTP {rss.status_code}")
        items = SafeET.fromstring(rss.content).iter("item")
        guids = [g for g in (i.findtext("guid") for i in items) if g]
        known = {r["source_ref"] for r in await conn.fetch(
            "SELECT source_ref FROM hazards WHERE source = 'sachet' AND source_ref = ANY($1::text[])", guids)}
        backlog = [g for g in guids if g not in known]  # RSS is newest first
        new = backlog[: self.max_new_per_poll]
        results = await asyncio.gather(*(self._alert(http, conn, g) for g in new), return_exceptions=True)
        hazards, skipped = [], 0
        for guid, result in zip(new, results, strict=True):
            if isinstance(result, SourceUnavailable):
                raise result  # the source itself is failing; let the runner mark it stale
            if isinstance(result, Exception):
                skipped += 1
                log.warning("sachet_alert_malformed", guid=guid, error=str(result)[:300])
            elif result is not None:
                hazards.append(result)
        log.info("sachet_polled", rss_items=len(guids), fetched=len(new), backlog=len(backlog) - len(new),
                 hazards=len(hazards), skipped=skipped)
        if new and skipped == len(new):
            # Every download failed (e.g. SACHET's per-IP 403 rate limit): the source is degraded,
            # not "fine with no alerts". Existing rows stay; freshness goes stale after the TTL.
            first_error = next(str(r) for r in results if isinstance(r, Exception))
            self.breaker.record_failure()  # 3 such polls open the breaker and pause SACHET for 15 min
            raise SourceUnavailable(self.name, f"all {skipped} alert downloads failed, e.g. {first_error[:150]}")
        cooling_down = time.monotonic() < self._polygon_blocked_until
        fallback = AREA_FALLBACK if cooling_down or any(h.raw.get("geometry_source") == AREA_FALLBACK for h in hazards) else None
        return FetchResult(hazards=hazards, facilities=[], skipped=skipped, used_fallback=fallback)
