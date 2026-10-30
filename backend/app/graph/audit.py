"""agent_events writer. Each write is its own autocommit statement so the SSE timeline shows
progress while the graph is still running."""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from app.core.logging import get_logger

log = get_logger(__name__)


class Audit:
    def __init__(self, pool: asyncpg.Pool, plan_id: str, thread_id: str):
        self.pool = pool
        self.plan_id = plan_id
        self.thread_id = thread_id

    async def __call__(self, node: str, event_type: str, message: str, payload: dict[str, Any] | None = None,
                       source_ids: list[str] | None = None) -> None:
        await self.pool.execute(
            """INSERT INTO agent_events (plan_id, thread_id, node, event_type, message, payload, source_ids)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            self.plan_id, self.thread_id, node, event_type, message[:2000],
            json.loads(json.dumps(payload or {}, default=str)), source_ids or [],
        )  # fmt: skip
        log.info("agent_event", plan_id=self.plan_id, node=node, event_type=event_type, message=message[:200])
