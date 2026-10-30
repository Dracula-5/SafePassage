"""Settings for Instance B. Extends the shared CommonSettings."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import NoDecode

from app.core.config import CommonSettings


class SensesSettings(CommonSettings):
    # Demo region as min_lon,min_lat,max_lon,max_lat. Bounds polling (Open-Meteo grid,
    # Overpass, FIRMS); SACHET and GDACS are national feeds and are not clipped.
    region_bbox: Annotated[tuple[float, float, float, float], NoDecode] = Field(description="min_lon,min_lat,max_lon,max_lat")
    region_name: str = "Andhra Pradesh"
    http_user_agent: str = "ReliefRoute/0.1 (+https://github.com/Dracula-5/SafePassage)"

    sachet_rss_url: str = "https://sachet.ndma.gov.in/cap_public_website/rss/rss_india.xml"
    sachet_poll_s: int = 120
    gdacs_poll_s: int = 900
    gdacs_country: str = "India"
    gdacs_lookback_days: int = 14
    open_meteo_poll_s: int = 3 * 3600
    open_meteo_grid_deg: float = Field(default=0.5, gt=0.05)
    firms_map_key: str | None = None          # NASA FIRMS MAP_KEY; source disabled when unset
    firms_poll_s: int = 1800
    overpass_urls: list[str] = Field(default_factory=lambda: [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.private.coffee/api/interpreter",
    ])
    facilities_poll_s: int = 24 * 3600
    # Fallback for facilities when every Overpass mirror fails (built by infra/osm/extract_facilities.sh).
    facilities_extract_path: Path = Path.home() / "reliefroute-data" / "osm" / "facilities.geojsonseq"

    @field_validator("region_bbox", mode="before")
    @classmethod
    def _parse_bbox(cls, value: object) -> object:
        if isinstance(value, str):
            parts = [float(p) for p in value.split(",")]
            if len(parts) != 4:
                raise ValueError("REGION_BBOX needs 4 comma-separated numbers")
            return tuple(parts)
        return value


@lru_cache
def get_senses_settings() -> SensesSettings:
    return SensesSettings()  # type: ignore[call-arg]
