"""Publish/consume round trip on a real Redis. Needs TEST_REDIS_URL."""

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from reliefroute_contracts.events import PlanRequested

from app.core import bus
from app.core.config import get_common_settings

pytestmark = pytest.mark.integration
REDIS_URL = os.environ.get("TEST_REDIS_URL")


@pytest.fixture
async def live_bus(monkeypatch):
    if not REDIS_URL:
        pytest.skip("TEST_REDIS_URL not set")
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    get_common_settings.cache_clear()
    await bus.close_redis()
    yield bus.get_redis()
    await bus.close_redis()
    get_common_settings.cache_clear()


async def test_publish_consume_and_deadletter(live_bus, monkeypatch):
    stream = "plan.requested"
    group = f"test-{uuid4().hex[:6]}"
    await bus.ensure_group(stream, group)  # created at "$": only sees events after this point
    monkeypatch.setattr(bus, "BLOCK_MS", 200)

    payload = PlanRequested(plan_id=uuid4(), trigger_kind="manual", requested_by="test")
    sent = await bus.publish(stream, payload, dedupe_key=str(payload.plan_id))
    await live_bus.xadd(stream, {"data": '{"not": "an envelope"}'})

    received = []
    stop = asyncio.Event()

    async def handler(envelope):
        received.append(envelope)
        stop.set()

    await asyncio.wait_for(bus.consume([stream], group, "c1", handler, stop), timeout=10)
    assert [e.event_id for e in received] == [sent.event_id]

    # Poison entry: acked and parked, so nothing stays pending. Give the loop one more pass.
    stop2 = asyncio.Event()
    asyncio.get_running_loop().call_later(1.0, stop2.set)
    await bus.consume([stream], group, "c1", handler, stop2)
    pending = await live_bus.xpending(stream, group)
    assert pending["pending"] == 0
    dead = await live_bus.xrevrange(bus.DEAD_LETTER_PREFIX + stream, count=1)
    assert dead and "not" in dead[0][1]["data"]
    await live_bus.xgroup_destroy(stream, group)


def test_build_envelope_sets_producer_from_catalog():
    env = bus.build_envelope(
        "plan.requested", PlanRequested(plan_id=uuid4(), trigger_kind="manual", requested_by="t"))
    assert env.producer == "brain" and env.emitted_at <= datetime.now(UTC)
