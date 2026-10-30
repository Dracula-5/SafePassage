"""Redis Streams publish/consume following contracts/events.md.

publish() validates the envelope against the contract before XADD, so a malformed event
never reaches the other side. consume() acks only after the handler returns, re-claims
entries left pending by crashed consumers, and parks unparseable entries in a dead-letter
stream instead of retrying them forever.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from uuid import uuid4

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


async def _handle(stream: str, group: str, entry_id: str, fields: dict[str, str], handler: Handler) -> None:
    client = get_redis()
    try:
        envelope = parse_envelope(fields.get("data", ""))
    except ValidationError as exc:
        # Poison message: park it, ack it, keep the consumer moving.
        await client.xadd(DEAD_LETTER_PREFIX + stream, {"data": fields.get("data", ""), "error": str(exc)[:2000]})
        await client.xack(stream, group, entry_id)
        log.error("event_invalid_deadlettered", stream=stream, entry_id=entry_id)
        return
    try:
        await handler(envelope)
    except Exception:
        # Not acked: it stays pending and is re-claimed after RECLAIM_IDLE_MS.
        log.exception("event_handler_failed", stream=stream, entry_id=entry_id, event_id=str(envelope.event_id))
        return
    await client.xack(stream, group, entry_id)


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
        except (ConnectionError, TimeoutError, redis.ConnectionError, redis.TimeoutError) as exc:
            log.warning("redis_unavailable_retrying", error=str(exc))
            await asyncio.sleep(2)
