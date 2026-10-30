"""Settings for Instance A (Brain). Extends CommonSettings."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field

from .config import CommonSettings


class BrainSettings(CommonSettings):
    llm_provider: str = "gemini"
    gemini_api_key: str | None = None
    llm_model: str | None = None
    valhalla_url: str = Field(description="e.g. http://localhost:8002")
    planning_horizon_days_max: int = Field(default=3, ge=1, le=7)
    relief_points_per_plan: int = Field(default=5, ge=1, le=20)
    relief_search_radius_m: float = Field(default=15_000, gt=0)
    min_route_clearance_m: float = Field(default=100, ge=0)
    procurement_radius_m: float = Field(default=150_000, gt=0, description="suppliers farther than this are not asked")
    procurement_max_suppliers_per_item: int = Field(default=40, ge=1)
    vehicle_radius_m: float = Field(default=200_000, gt=0, description="vehicles farther than this are not planned")
    critic_max_attempts: int = Field(default=3, ge=1, le=5)
    auto_plan_severities: list[str] = Field(default_factory=lambda: ["severe", "extreme"])
    approval_weight_kg: float = Field(default=5_000, description="plans heavier than this always need approval")
    documents_dir: Path = Path.home() / "reliefroute-data" / "documents"
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None


@lru_cache
def get_brain_settings() -> BrainSettings:
    return BrainSettings()  # type: ignore[call-arg]
