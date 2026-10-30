"""Settings shared by both instances. Each side subclasses CommonSettings for its own keys."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CommonSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="development", description="development | production")
    database_url: str = Field(description="postgresql://user:pass@host:5432/db")
    redis_url: str = Field(description="redis://host:6379/0")
    log_level: str = "INFO"
    cors_origins: list[str] = Field(default_factory=list)
    operator_token: str | None = Field(default=None, description="Required by drill/plan/approve endpoints when set")
    # Default timeout for any outbound HTTP call; individual clients may tighten it.
    http_timeout_s: float = 10.0


@lru_cache
def get_common_settings() -> CommonSettings:
    return CommonSettings()  # type: ignore[call-arg]  # values come from the environment
