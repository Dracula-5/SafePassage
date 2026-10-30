"""Common shape of a polled source."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import asyncpg
import httpx

from app.core.resilience import CircuitBreaker

from .models import NormalizedFacility, NormalizedHazard


@dataclass
class FetchResult:
    hazards: list[NormalizedHazard]
    facilities: list[NormalizedFacility]
    used_fallback: str | None = None      # name of the fallback source actually used, if any
    skipped: int = 0                      # records dropped as malformed (logged individually)


class Source(Protocol):
    name: str
    poll_s: int
    ttl_s: int
    breaker: CircuitBreaker

    async def fetch(self, http: httpx.AsyncClient, conn: asyncpg.Pool) -> FetchResult: ...
