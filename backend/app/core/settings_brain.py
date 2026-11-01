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
    relief_min_distance_m: float = Field(default=1_500, ge=0, description="Beyond the widest routing margin, or nothing can reach it")
    min_route_clearance_m: float = Field(default=100, ge=0)
    procurement_radius_m: float = Field(default=150_000, gt=0, description="suppliers farther than this are not asked")
    procurement_max_suppliers_per_item: int = Field(default=40, ge=1)
    vehicle_radius_m: float = Field(default=200_000, gt=0, description="vehicles farther than this are not planned")
    planner_max_vehicles: int = Field(default=12, ge=1, description="Nearest vehicles offered to the solver for one plan")
    max_trips_per_vehicle: int = Field(default=3, ge=1, le=10, description="Goods planned per wave, in vehicle loads")
    max_shift_hours: float = Field(default=12, gt=0, le=72, description="Longest single route for one driver")
    off_route_m: float = Field(default=500, gt=0, description="Farther than this from the route counts as off route")
    off_route_fixes: int = Field(default=3, ge=1, description="Consecutive fixes before it is a deviation")
    arrival_radius_m: float = Field(default=300, gt=0, description="Within this of a stop counts as reached")
    offline_grace_s: int = Field(default=300, gt=0, description="Silence tolerated on a route before reassigning")
    stalled_after_s: int = Field(default=900, gt=0, description="No real movement for this long away from a stop")
    monitor_sweep_s: int = Field(default=30, gt=0)
    critic_max_attempts: int = Field(default=3, ge=1, le=5)
    auto_plan_severities: list[str] = Field(default_factory=lambda: ["severe", "extreme"])
    auto_approve: bool = Field(default=True, description="False sends every plan to a person")
    auto_approve_max_kg: float = Field(default=300_000, description="See agents/approval.py")
    auto_approve_max_unserved: float = Field(default=0.2, ge=0, le=1)
    auto_approve_min_clearance_m: float = Field(default=200, ge=0)
    next_wave_max: int = Field(default=10, ge=0, description="Waves per alert that start by themselves. 0 turns them off")
    next_wave_window_minutes: int = Field(default=30, gt=0, description="Trucks that finished longer ago start no wave")
    next_wave_retry_minutes: int = Field(default=5, gt=0, description="A wave that found no stock or truck tries again after this")
    documents_dir: Path = Path.home() / "reliefroute-data" / "documents"
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None
    # A drill tells real suppliers to prepare goods unless it stays silent. Off, even with Twilio set.
    drill_send_messages: bool = False
    max_replans_per_hour: int = 6            # per vehicle, for off-route and stalled only


@lru_cache
def get_brain_settings() -> BrainSettings:
    return BrainSettings()  # type: ignore[call-arg]
