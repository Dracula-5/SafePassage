import httpx
import pytest

from app.core.resilience import BreakerState, CircuitBreaker, SourceUnavailable, resilient_get


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_breaker_opens_then_half_opens_then_closes():
    clock = Clock()
    breaker = CircuitBreaker("t", failure_threshold=2, reset_after_s=30, clock=clock)
    breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED
    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN and not breaker.allow()
    clock.now = 31
    assert breaker.state is BreakerState.HALF_OPEN and breaker.allow()
    breaker.record_failure()  # failed probe re-opens
    assert breaker.state is BreakerState.OPEN
    clock.now = 62
    breaker.record_success()
    assert breaker.state is BreakerState.CLOSED


def _client(statuses: list[int]) -> tuple[httpx.AsyncClient, list[int]]:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(statuses[min(len(calls) - 1, len(statuses) - 1)])

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


async def test_retries_then_succeeds():
    client, calls = _client([503, 200])
    breaker = CircuitBreaker("t")
    response = await resilient_get(client, "http://x", breaker, retries=2, backoff_base_s=0)
    assert response.status_code == 200 and len(calls) == 2
    assert breaker.state is BreakerState.CLOSED


async def test_exhausted_retries_raise_and_count_failure():
    client, calls = _client([503])
    breaker = CircuitBreaker("t", failure_threshold=1)
    with pytest.raises(SourceUnavailable):
        await resilient_get(client, "http://x", breaker, retries=2, backoff_base_s=0)
    assert len(calls) == 3
    assert breaker.state is BreakerState.OPEN


async def test_open_breaker_short_circuits():
    client, calls = _client([200])
    breaker = CircuitBreaker("t", failure_threshold=1)
    breaker.record_failure()
    with pytest.raises(SourceUnavailable, match="circuit open"):
        await resilient_get(client, "http://x", breaker)
    assert calls == []


async def test_timeout_is_retryable():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(SourceUnavailable, match="ReadTimeout"):
        await resilient_get(client, "http://x", CircuitBreaker("t"), retries=1, backoff_base_s=0)


async def test_client_error_is_returned_not_retried():
    client, calls = _client([404])
    response = await resilient_get(client, "http://x", CircuitBreaker("t"), backoff_base_s=0)
    assert response.status_code == 404 and len(calls) == 1
