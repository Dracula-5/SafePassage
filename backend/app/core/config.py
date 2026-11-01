"""Settings shared by both instances. Each side subclasses CommonSettings for its own keys."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


SIMULATED_LABEL = "TEST-SIM-GPS"  # starts every simulated vehicle's label, so it can never pass as a real one


class CommonSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # A Literal, so a typo like APP_ENV=prod stops the start instead of switching the checks off.
    app_env: Literal["development", "production"] = "development"
    database_url: str = Field(description="postgresql://user:pass@host:5432/db")
    redis_url: str = Field(description="redis://host:6379/0")
    # Render's Key Value has no password on its private address, and no public one when its allow list is empty.
    redis_private_network: bool = False
    log_level: str = "INFO"
    cors_origins: list[str] = Field(default_factory=list)
    operator_token: str | None = Field(default=None, description="See core/auth.py. Required in production")
    enrolment_code: str | None = Field(default=None, description="See core/auth.py. Required in production")
    rate_limit_enabled: bool = True
    max_body_bytes: int = 1_000_000
    public_url: str = "http://localhost:3000"  # the start of every link in an email
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    mail_from: str | None = None
    demo_fleet: bool = False  # simulated drivers for presentations, see telemetry/demo.py
    # Default timeout for any outbound HTTP call; individual clients may tighten it.
    http_timeout_s: float = 10.0


@lru_cache
def get_common_settings() -> CommonSettings:
    return CommonSettings()  # type: ignore[call-arg]  # values come from the environment
