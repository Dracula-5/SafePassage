"""Redis Streams publish/consume following contracts/events.md.

publish() validates the envelope against the contract before XADD, so a malformed event
never reaches the other side. consume() acks only after the handler returns, re-claims
entries left pending by crashed consumers, and parks entries it can never process in a
dead-letter stream instead of retrying them forever: unparseable ones at once, and ones whose
handler keeps failing after MAX_DELIVERIES. An outage is not the event's fault, so failures
that look like one are retried for as long as the outage lasts.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import redis.asyncio as redis
from pydantic import BaseModel, ValidationError
from redis.exceptions import ResponseError
from reliefroute_contracts.events import EVENT_PRODUCERS, EventEnvelope, envelope_adapter, parse_envelope
from reliefroute_contracts.streams import STREAM_MAXLEN

from .config import get_common_settings
from .logging import get_logger

log = get_logger(__name__)
_client: redis.Redis | None = None

RECLAIM_IDLE_MS = 60_000
BLOCK_MS = 5_000
DEAD_LETTER_PREFIX = "deadletter."
DEAD_LETTER_MAXLEN = 1_000
MAX_DELIVERIES = 5
RETRY_AFTER_REDIS_ERROR_S = 2
OUTAGE_ERRORS = (OSError, TimeoutError, redis.RedisError, asyncpg.PostgresConnectionError, asyncpg.InterfaceError,
                 asyncpg.CannotConnectNowError)  # fmt: skip
HEARTBEAT_EVERY_S = 30
HEARTBEAT_TTL_S = 90

Handler = Callable[[EventEnvelope], Awaitable[None]]


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(
            get_common_settings().redis_url, socket_timeout=10, socket_connect_timeout=5, decode_responses=True
        )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def build_envelope(event_type: str, payload: BaseModel, dedupe_key: str | None = None) -> EventEnvelope:
    """Wrap a payload in a validated envelope. Raises KeyError / ValidationError on misuse."""
    return envelope_adapter.validate_python(
        {
            "event_id": uuid4(),
            "event_type": event_type,
            "emitted_at": datetime.now(UTC),
            "producer": EVENT_PRODUCERS[event_type],
            "dedupe_key": dedupe_key,
            "payload": payload.model_dump(mode="json"),
        }
    )


async def publish(event_type: str, payload: BaseModel, dedupe_key: str | None = None) -> EventEnvelope:
    envelope = build_envelope(event_type, payload, dedupe_key)
    data = envelope_adapter.dump_json(envelope).decode()
    entry_id = await get_redis().xadd(event_type, {"data": data}, maxlen=STREAM_MAXLEN, approximate=True)
    log.info("event_published", event_type=event_type, event_id=str(envelope.event_id), entry_id=entry_id)
    return envelope


async def try_publish(event_type: str, payload: BaseModel, dedupe_key: str | None = None) -> bool:
    """Publish when the database row is already committed and something else will catch up if this
    fails (the worker's sweeps). A Redis outage must not turn a recorded decision into an API error."""
    try:
        await publish(event_type, payload, dedupe_key)
        return True
    except (redis.RedisError, OSError) as exc:
        log.warning("event_not_published", event_type=event_type, error=str(exc))
        return False


async def ensure_group(stream: str, group: str) -> None:
    try:
        await get_redis().xgroup_create(stream, group, id="$", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def _park(stream: str, group: str, entry_id: str, data: str, error: str) -> None:
    client = get_redis()
    await client.xadd(DEAD_LETTER_PREFIX + stream, {"data": data, "error": error[:2000]},
                      maxlen=DEAD_LETTER_MAXLEN, approximate=True)  # fmt: skip
    await client.xack(stream, group, entry_id)


async def _deliveries(stream: str, group: str, entry_id: str) -> int:
    pending = await get_redis().xpending_range(stream, group, min=entry_id, max=entry_id, count=1)
    return pending[0]["times_delivered"] if pending else 0


async def _handle(stream: str, group: str, entry_id: str, fields: dict[str, str], handler: Handler) -> None:
    data = fields.get("data", "")
    try:
        envelope = parse_envelope(data)
    except ValidationError as exc:
        await _park(stream, group, entry_id, data, str(exc))
        log.error("event_invalid_deadlettered", stream=stream, entry_id=entry_id)
        return
    try:
        await handler(envelope)
    except OUTAGE_ERRORS:
        # Not acked: it stays pending and is re-claimed after RECLAIM_IDLE_MS, however long this lasts.
        log.exception("event_handler_outage", stream=stream, entry_id=entry_id, event_id=str(envelope.event_id))
        return
    except Exception as exc:
        log.exception("event_handler_failed", stream=stream, entry_id=entry_id, event_id=str(envelope.event_id))
        if await _deliveries(stream, group, entry_id) >= MAX_DELIVERIES:
            await _park(stream, group, entry_id, data, f"{type(exc).__name__}: {exc}")
            log.error("event_gave_up_deadlettered", stream=stream, entry_id=entry_id)
        return
    await get_redis().xack(stream, group, entry_id)


async def consume(
    streams: Sequence[str], group: str, consumer: str, handler: Handler, stop: asyncio.Event | None = None
) -> None:
    """Run until `stop` is set. Handler failures are logged, never crash the loop."""
    client = get_redis()
    for stream in streams:
        await ensure_group(stream, group)
    while not (stop and stop.is_set()):
        try:
            for stream in streams:
                _, claimed, _ = await client.xautoclaim(stream, group, consumer, RECLAIM_IDLE_MS, count=50)
                for entry_id, fields in claimed:
                    if fields:  # entries trimmed by MAXLEN come back empty
                        await _handle(stream, group, entry_id, fields, handler)
            response = await client.xreadgroup(group, consumer, {s: ">" for s in streams}, count=50, block=BLOCK_MS)
            for stream, entries in response or []:
                for entry_id, fields in entries:
                    await _handle(stream, group, entry_id, fields, handler)
        except ResponseError as exc:
            if "NOGROUP" not in str(exc):
                raise
            # Redis lost its data (restarted without its volume). The sweeps catch up on what was missed.
            log.warning("consumer_group_lost_recreating", group=group)
            for stream in streams:
                await ensure_group(stream, group)
        except (ConnectionError, TimeoutError, redis.ConnectionError, redis.TimeoutError) as exc:
            log.warning("redis_unavailable_retrying", error=str(exc))
            await asyncio.sleep(RETRY_AFTER_REDIS_ERROR_S)


async def dead_letter_count() -> int:
    client = get_redis()
    return sum([await client.xlen(key) async for key in client.scan_iter(match=DEAD_LETTER_PREFIX + "*")])


def _heartbeat_key(worker: str) -> str:
    return f"heartbeat:{worker}"


async def heartbeat(worker: str) -> None:
    """Runs for the life of a worker. A worker that stops beating shows as stale on the dashboard:
    otherwise a dead brain looks exactly like a quiet day."""
    while True:
        try:
            await get_redis().set(_heartbeat_key(worker), datetime.now(UTC).isoformat(), ex=HEARTBEAT_TTL_S)
        except (redis.RedisError, OSError) as exc:
            log.warning("heartbeat_not_sent", worker=worker, error=str(exc))
        await asyncio.sleep(HEARTBEAT_EVERY_S)


async def last_heartbeat(worker: str) -> str | None:
    return await get_redis().get(_heartbeat_key(worker))
