"""GET /api/stream: Server-Sent Events for the dashboard.

Events (contracts/openapi.yaml):
  agent_event    rows of agent_events, tailed by id (the contract lets B read this table for SSE);
                 the SSE id is the agent_events id, so Last-Event-ID resumes without gaps
  hazard         hazard.updated payloads from Redis Streams
  vehicle        a vehicle whose position or liveness changed
  source_health  a source whose freshness or breaker state changed
  plan           a plan whose status changed, from A's v_plan_status view

EventSource cannot send headers, so a dashboard swaps its operator token for a short-lived ticket
at POST /api/stream/ticket. Connections are capped: each one polls the database every second, so
without a cap a few hundred idle tabs would use up the pool.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from redis.exceptions import RedisError
from reliefroute_contracts.events import parse_envelope
from reliefroute_contracts.streams import HAZARD_UPDATED

from app.core.auth import require_operator, require_stream_access, stream_ticket
from app.core.bus import get_redis
from app.core.db import get_pool
from app.core.logging import get_logger

from ..telemetry.router import VEHICLE_SELECT, vehicle_out

router = APIRouter(tags=["senses"])
log = get_logger(__name__)
TICK_S = 1.0
VEHICLE_EVERY_S = 3.0
HEALTH_EVERY_S = 10.0
PLAN_EVERY_S = 2.0
RECENT_PLANS = 50
KEEPALIVE_S = 15.0
BACKLOG = 200  # agent events sent on a fresh connection
MAX_STREAMS = 50
MAX_EVENT_ID = 2**63 - 1  # agent_events.id is a bigint
DEPENDENCY_ERRORS = (OSError, ConnectionError, RedisError, asyncpg.PostgresError, asyncpg.InterfaceError)
open_streams = 0


def sse(event: str, data: object, event_id: str | None = None) -> str:
    head = f"id: {event_id}\n" if event_id else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _agent_events(after_id: int, plan_id: UUID | None) -> list[dict]:
    rows = await (await get_pool()).fetch(
        """SELECT id, plan_id, node::text, event_type::text, message, payload, source_ids, created_at
           FROM agent_events WHERE id > $1 AND ($2::uuid IS NULL OR plan_id = $2) ORDER BY id LIMIT 500""",
        after_id, plan_id,
    )  # fmt: skip
    return [dict(r) | {"plan_id": str(r["plan_id"]) if r["plan_id"] else None} for r in rows]


async def _plans(plan_id: UUID | None) -> list[dict]:
    """Newest plans, in the PlanSummary shape of contracts/openapi.yaml."""
    rows = await (await get_pool()).fetch(
        """SELECT id, parent_plan_id, status::text, trigger_kind::text, hazard_ids, is_drill, requires_approval, summary,
                  created_at, updated_at
           FROM v_plan_status WHERE $1::uuid IS NULL OR id = $1 ORDER BY updated_at DESC LIMIT $2""",
        plan_id, RECENT_PLANS,
    )  # fmt: skip
    return [dict(r) | {"id": str(r["id"]), "parent_plan_id": str(r["parent_plan_id"]) if r["parent_plan_id"] else None,
                       "hazard_ids": [str(h) for h in r["hazard_ids"]]} for r in reversed(rows)]  # fmt: skip


async def event_stream(request: Request, plan_id: UUID | None, last_event_id: int | None) -> AsyncIterator[str]:
    global open_streams
    try:
        async for chunk in _events(request, plan_id, last_event_id):
            yield chunk
    finally:
        open_streams -= 1


async def _events(request: Request, plan_id: UUID | None, last_event_id: int | None) -> AsyncIterator[str]:
    pool = await get_pool()
    redis = get_redis()
    if last_event_id is None:  # fresh connection: start with the most recent backlog
        last_event_id = await pool.fetchval("SELECT COALESCE(max(id), 0) - $1 FROM agent_events", BACKLOG) or 0
    hazard_cursor = "$"
    vehicles_seen: dict[str, tuple] = {}
    health_seen: dict[str, tuple] = {}
    plans_seen: dict[str, object] = {}
    next_vehicle = next_health = next_keepalive = next_plan = 0.0
    yield "retry: 3000\n\n"
    while not await request.is_disconnected():
        now = time.monotonic()
        try:
            for row in await _agent_events(last_event_id, plan_id):
                last_event_id = row["id"]
                yield sse("agent_event", row, str(row["id"]))
            entries = await redis.xread({HAZARD_UPDATED: hazard_cursor}, count=100, block=None)
            if hazard_cursor == "$":  # resolve "$" to a concrete id so nothing is missed between reads
                last = await redis.xrevrange(HAZARD_UPDATED, count=1)
                hazard_cursor = last[0][0] if last else "0-0"
            for _, items in entries or []:
                for entry_id, fields in items:
                    hazard_cursor = entry_id
                    try:
                        payload = parse_envelope(fields["data"]).payload
                    except ValidationError:  # the bus parks these; one bad entry must not end the stream
                        log.warning("stream_skipped_invalid_event", entry_id=entry_id)
                        continue
                    yield sse("hazard", payload.model_dump(mode="json"))
            if now >= next_vehicle:
                next_vehicle = now + VEHICLE_EVERY_S
                for r in await pool.fetch(VEHICLE_SELECT):
                    key = (r["last_seen_at"], r["is_offline"], r["status"])
                    if vehicles_seen.get(str(r["id"])) != key:
                        vehicles_seen[str(r["id"])] = key
                        yield sse("vehicle", vehicle_out(r))
            if now >= next_plan:
                next_plan = now + PLAN_EVERY_S
                for row in await _plans(plan_id):
                    if plans_seen.get(row["id"]) != row["updated_at"]:
                        plans_seen[row["id"]] = row["updated_at"]
                        yield sse("plan", row)
            if now >= next_health:
                next_health = now + HEALTH_EVERY_S
                for r in await pool.fetch("SELECT * FROM v_source_freshness"):
                    key = (r["is_stale"], r["breaker_state"], r["last_success_at"], r["fallback_source"])
                    if health_seen.get(r["source"]) != key:
                        health_seen[r["source"]] = key
                        yield sse("source_health", dict(r))
            if now >= next_keepalive:
                next_keepalive = now + KEEPALIVE_S
                yield ": keepalive\n\n"
        except DEPENDENCY_ERRORS as exc:  # DB/Redis hiccup: tell the client, keep the stream
            log.warning("stream_dependency_error", error=str(exc))
            yield sse("error", {"message": "live data temporarily unavailable"})
            await asyncio.sleep(3)
        await asyncio.sleep(TICK_S)


def _resume_from(last_event_id: str | None) -> int | None:
    if not (last_event_id and last_event_id.isascii() and last_event_id.isdigit() and len(last_event_id) <= 19):
        return None
    value = int(last_event_id)
    return value if value <= MAX_EVENT_ID else None


@router.post("/api/stream/ticket", dependencies=[Depends(require_operator)])
async def new_stream_ticket() -> dict:
    return stream_ticket()


@router.get("/api/stream", dependencies=[Depends(require_stream_access)])
async def stream(
    request: Request,
    plan_id: Annotated[UUID | None, Query()] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    global open_streams
    if open_streams >= MAX_STREAMS:
        raise HTTPException(503, "too many live connections; try again shortly")
    open_streams += 1
    resume = _resume_from(last_event_id)
    return StreamingResponse(event_stream(request, plan_id, resume), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})  # fmt: skip
