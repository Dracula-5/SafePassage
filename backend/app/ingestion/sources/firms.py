"""NASA FIRMS active fires (VIIRS S-NPP near real time) inside the region.

Needs a FIRMS MAP_KEY (free, https://firms.modaps.eosdis.nasa.gov/api/map_key/). Without it
the source is not scheduled and /api/sources/health has no row for it: nothing is invented.
Each detection becomes a hazard covering its pixel footprint: `scan` x `track` (km, from the
CSV) centred on the detection.
"""

from __future__ import annotations

import csv
import io
import math
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
from reliefroute_contracts.enums import HazardSeverity, HazardSource, HazardType
from shapely import box

from app.core.logging import get_logger
from app.core.resilience import CircuitBreaker, SourceUnavailable, resilient_get

from ..base import FetchResult
from ..models import NormalizedHazard

log = get_logger(__name__)
URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/VIIRS_SNPP_NRT/{bbox}/1"
CONFIDENCE = {"h": HazardSeverity.SEVERE, "n": HazardSeverity.MODERATE, "l": HazardSeverity.MINOR}


def parse_firms_csv(text: str) -> list[NormalizedHazard]:
    hazards = []
    for row in csv.DictReader(io.StringIO(text)):
        lat, lon = float(row["latitude"]), float(row["longitude"])
        half_w = float(row["scan"]) / 2 / (111.32 * math.cos(math.radians(lat)))  # km -> deg lon
        half_h = float(row["track"]) / 2 / 110.57                                  # km -> deg lat
        acq = datetime.strptime(f"{row['acq_date']} {int(row['acq_time']):04d}", "%Y-%m-%d %H%M").replace(tzinfo=UTC)
        ref = f"{row['acq_date']}T{int(row['acq_time']):04d}:{lat:.4f},{lon:.4f}"
        hazards.append(NormalizedHazard(
            source=HazardSource.FIRMS, source_ref=ref, dedupe_key=f"firms:{ref}",
            hazard_type=HazardType.WILDFIRE, severity=CONFIDENCE.get(row.get("confidence", "n"), HazardSeverity.MODERATE),
            headline=f"Active fire detected (VIIRS, FRP {row.get('frp', '?')} MW)",
            geom=box(lon - half_w, lat - half_h, lon + half_w, lat + half_h),
            effective_at=acq, expires_at=acq + timedelta(hours=24), issued_at=acq, raw=dict(row),
        ))  # fmt: skip
    return hazards


class FirmsSource:
    name = "firms"
    ttl_s = 3 * 3600

    def __init__(self, map_key: str, bbox: tuple[float, float, float, float], poll_s: int):
        self.map_key = map_key
        self.bbox = ",".join(str(v) for v in bbox)
        self.poll_s = poll_s
        self.breaker = CircuitBreaker("firms", failure_threshold=3, reset_after_s=600)

    async def fetch(self, http: httpx.AsyncClient, conn: asyncpg.Pool) -> FetchResult:
        resp = await resilient_get(http, URL.format(key=self.map_key, bbox=self.bbox), self.breaker, timeout_s=60)
        if resp.status_code != 200 or not resp.text.startswith("latitude"):
            raise SourceUnavailable(self.name, f"HTTP {resp.status_code}: {resp.text[:120]}")
        hazards = parse_firms_csv(resp.text)
        log.info("firms_polled", detections=len(hazards))
        return FetchResult(hazards=hazards, facilities=[])
