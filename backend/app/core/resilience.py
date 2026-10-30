"""Timeouts, retries and circuit breaking for every outbound HTTP call.

Callers catch SourceUnavailable and switch to their fallback source, flagging the data as
stale. Nothing here invents data.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

import httpx

from .logging import get_logger

log = get_logger(__name__)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class SourceUnavailable(Exception):
    """Raised when a source is down, its breaker is open, or retries are exhausted."""

    def __init__(self, source: str, reason: str):
        super().__init__(f"{source}: {reason}")
        self.source = source
        self.reason = reason


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Opens after `failure_threshold` consecutive failures; allows one probe after `reset_after_s`."""

    name: str
    failure_threshold: int = 3
    reset_after_s: float = 60.0
    clock: Callable[[], float] = time.monotonic
    _failures: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False)

    @property
    def state(self) -> BreakerState:
        if self._opened_at is None:
            return BreakerState.CLOSED
        if self.clock() - self._opened_at >= self.reset_after_s:
            return BreakerState.HALF_OPEN
        return BreakerState.OPEN

    def allow(self) -> bool:
        return self.state is not BreakerState.OPEN

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        # A failed half-open probe re-opens immediately.
        if self._failures >= self.failure_threshold or self.state is BreakerState.HALF_OPEN:
            if self.state is not BreakerState.OPEN:
                log.warning("circuit_opened", breaker=self.name, failures=self._failures)
            self._opened_at = self.clock()


async def resilient_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    breaker: CircuitBreaker,
    *,
    retries: int = 2,
    timeout_s: float = 10.0,
    backoff_base_s: float = 0.5,
    **kwargs,
) -> httpx.Response:
    """Send a request with timeout, jittered exponential retry and breaker accounting.

    Returns the response for any non-retryable status (caller decides about 4xx).
    Raises SourceUnavailable when the breaker is open or all attempts failed.
    """
    if not breaker.allow():
        raise SourceUnavailable(breaker.name, "circuit open")
    last_error = "unknown"
    for attempt in range(retries + 1):
        try:
            response = await client.request(method, url, timeout=timeout_s, **kwargs)
            if response.status_code not in RETRYABLE_STATUS:
                breaker.record_success()
                return response
            last_error = f"HTTP {response.status_code}"
        except httpx.TransportError as exc:  # includes timeouts and connection errors
            last_error = f"{type(exc).__name__}: {exc}"
        log.warning("http_attempt_failed", source=breaker.name, attempt=attempt + 1, error=last_error)
        if attempt < retries:
            await asyncio.sleep(backoff_base_s * (2**attempt) * (0.5 + random.random()))
    breaker.record_failure()
    raise SourceUnavailable(breaker.name, last_error)


async def resilient_get(client: httpx.AsyncClient, url: str, breaker: CircuitBreaker, **kwargs) -> httpx.Response:
    return await resilient_request(client, "GET", url, breaker, **kwargs)
