"""Open-Meteo forecast rainfall over the region, classified with IMD's official 24-hour
rainfall categories. Fallback/complement to SACHET's IMD alerts.

IMD categories (mm in 24 h): heavy 64.5-115.5, very heavy 115.6-204.4, extremely heavy >= 204.5.
Source: IMD forecast terminology for 24-hour rainfall intensity (mausam.imd.gov.in).
Each grid cell whose forecast daily total reaches "heavy" becomes a hazard for that day.
The cell is the grid square around the forecast point, i.e. the area that point represents.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import asyncpg
import httpx
from reliefroute_contracts.enums import HazardSeverity, HazardSource, HazardType
from shapely import box

from app.core.logging import get_logger
from app.core.resilience import CircuitBreaker, SourceUnavailable, resilient_get

from ..base import FetchResult
from ..models import NormalizedHazard

log = get_logger(__name__)
URL = "https://api.open-meteo.com/v1/forecast"
IST = ZoneInfo("Asia/Kolkata")
BATCH = 100  # locations per request

# (minimum mm/24h, IMD term, severity)
IMD_CATEGORIES = [
    (204.5, "extremely heavy rainfall", HazardSeverity.EXTREME),
    (115.6, "very heavy rainfall", HazardSeverity.SEVERE),
    (64.5, "heavy rainfall", HazardSeverity.MODERATE),
]


def classify_rain(mm: float | None) -> tuple[str, HazardSeverity] | None:
    if mm is None:
        return None
    for threshold, term, severity in IMD_CATEGORIES:
        if mm >= threshold:
            return term, severity
    return None


def grid(bbox: tuple[float, float, float, float], step: float) -> list[tuple[float, float]]:
    """Cell-centre points (lon, lat) covering bbox."""
    x0, y0, x1, y1 = bbox
    pts = []
    lat = y0 + step / 2
    while lat < y1:
        lon = x0 + step / 2
        while lon < x1:
            pts.append((round(lon, 4), round(lat, 4)))
            lon += step
        lat += step
    return pts


def cell_hazards(points: list[tuple[float, float]], payload: list[dict], step: float) -> list[NormalizedHazard]:
    hazards = []
    for (lon, lat), forecast in zip(points, payload, strict=True):
        daily = forecast.get("daily") or {}
        for day, mm in zip(daily.get("time", []), daily.get("precipitation_sum", []), strict=True):
            category = classify_rain(mm)
            if category is None:
                continue
            term, severity = category
            d = date.fromisoformat(day)
            hazards.append(NormalizedHazard(
                source=HazardSource.OPEN_METEO, source_ref=f"{lat},{lon}:{day}",
                dedupe_key=f"open_meteo:{lat},{lon}:{day}",
                hazard_type=HazardType.HEAVY_RAIN, severity=severity,
                headline=f"Forecast {term}: {mm:.0f} mm on {day} (IMD category)",
                area_desc=f"grid cell {step} deg around {lat},{lon}",
                geom=box(lon - step / 2, lat - step / 2, lon + step / 2, lat + step / 2),
                effective_at=datetime.combine(d, time.min, IST),
                expires_at=datetime.combine(d + timedelta(days=1), time.min, IST),
                raw={"precipitation_sum_mm": mm, "date": day, "lat": lat, "lon": lon,
                     "model_point": [forecast.get("latitude"), forecast.get("longitude")]},
            ))  # fmt: skip
    return hazards


class OpenMeteoSource:
    name = "open_meteo"
    ttl_s = 6 * 3600

    def __init__(self, bbox: tuple[float, float, float, float], step: float, poll_s: int, forecast_days: int = 3):
        self.points = grid(bbox, step)
        self.step = step
        self.poll_s = poll_s
        self.forecast_days = forecast_days
        self.breaker = CircuitBreaker("open_meteo", failure_threshold=3, reset_after_s=600)

    async def fetch(self, http: httpx.AsyncClient, conn: asyncpg.Pool) -> FetchResult:
        hazards = []
        for i in range(0, len(self.points), BATCH):
            chunk = self.points[i : i + BATCH]
            params = {"latitude": ",".join(str(p[1]) for p in chunk), "longitude": ",".join(str(p[0]) for p in chunk),
                      "daily": "precipitation_sum", "forecast_days": self.forecast_days, "timezone": "Asia/Kolkata"}  # fmt: skip
            resp = await resilient_get(http, URL, self.breaker, params=params, timeout_s=30)
            if resp.status_code != 200:
                raise SourceUnavailable(self.name, f"HTTP {resp.status_code}: {resp.text[:200]}")
            payload = resp.json()
            hazards.extend(cell_hazards(chunk, payload if isinstance(payload, list) else [payload], self.step))
        log.info("open_meteo_polled", points=len(self.points), hazards=len(hazards))
        return FetchResult(hazards=hazards, facilities=[])
