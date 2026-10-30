"""Runs one poll of a source end to end: fetch -> upsert (one transaction) -> events -> health.

Failure policy (confidence-aware degradation): if the source fails, existing rows are kept,
source_health records the failure and the breaker state, and v_source_freshness turns
`is_stale` once the TTL passes. Nothing is fabricated to fill the gap.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg
import httpx

from app.core.logging import get_logger
from app.core.resilience import SourceUnavailable

from .base import Source
from .store import publish_hazard_events, record_source_health, upsert_facilities, upsert_hazard

log = get_logger(__name__)


@dataclass
class PollOutcome:
    source: str
    ok: bool
    hazards_seen: int = 0
    events: int = 0
    facilities: int = 0
    skipped: int = 0
    fallback: str | None = None
    error: str | None = None


async def poll_once(source: Source, http: httpx.AsyncClient, pool: asyncpg.Pool) -> PollOutcome:
    try:
        result = await source.fetch(http, pool)
    except (SourceUnavailable, httpx.HTTPError, ValueError) as exc:
        error = f"{type(exc).__name__}: {exc}"[:500]
        await record_source_health(pool, source.name, ok=False, ttl_seconds=source.ttl_s,
                                   breaker_state=source.breaker.state.value, error=error)  # fmt: skip
        log.warning("poll_failed", source=source.name, error=error)
        return PollOutcome(source=source.name, ok=False, error=error)

    events = []
    async with pool.acquire() as conn, conn.transaction():
        for hazard in result.hazards:
            event = await upsert_hazard(conn, hazard)
            if event:
                events.append(event)
        written = await upsert_facilities(conn, result.facilities)
    await publish_hazard_events(events)
    await record_source_health(
        pool, source.name, ok=True, ttl_seconds=source.ttl_s, breaker_state=source.breaker.state.value,
        error=f"used fallback {result.used_fallback}" if result.used_fallback else None,
        fallback_source=result.used_fallback,
    )  # fmt: skip
    outcome = PollOutcome(source=source.name, ok=True, hazards_seen=len(result.hazards), events=len(events),
                          facilities=written, skipped=result.skipped, fallback=result.used_fallback)  # fmt: skip
    log.info("poll_done", **outcome.__dict__)
    return outcome
