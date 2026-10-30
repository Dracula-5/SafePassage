"""Brain worker process: runs plans from Redis Streams.

  plan.requested  -> start the graph (once per plan: pending -> planning is claimed atomically)
  plan.approved   -> resume the graph paused at the approval gate
  hazard.updated  -> auto-create a plan for new drills and for severe/extreme flood-type hazards

Run from backend/:  python -m app.graph.worker
"""

from __future__ import annotations

import asyncio
import socket

import httpx
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from reliefroute_contracts.events import EventEnvelope, PlanRequested
from reliefroute_contracts.streams import GROUP_BRAIN, HAZARD_UPDATED, PLAN_APPROVED, PLAN_REQUESTED

from app.core import bus
from app.core.db import close_pool, get_pool
from app.core.logging import configure_logging, get_logger
from app.core.settings_brain import get_brain_settings
from app.dispatch.messaging import Messenger
from app.llm.factory import build_llm
from app.routing.valhalla import ValhallaClient

from . import plans
from .audit import Audit
from .build import PlanRunner
from .nodes import Deps

log = get_logger(__name__)
AUTO_PLAN_TYPES = {"flood", "cyclone", "heavy_rain", "landslide", "tsunami", "earthquake"}


async def request_plan(pool, *, trigger_kind: str, trigger_ref: str | None, hazard_ids: list[str], requested_by: str,
                       idempotency_key: str | None) -> tuple[dict, bool]:
    row, created = await plans.create_plan(pool, trigger_kind=trigger_kind, trigger_ref=trigger_ref,
                                           hazard_ids=hazard_ids, requested_by=requested_by,
                                           idempotency_key=idempotency_key)  # fmt: skip
    if created:
        await bus.publish(PLAN_REQUESTED, PlanRequested(plan_id=row["id"], trigger_kind=trigger_kind,
                                                        trigger_ref=trigger_ref, hazard_ids=hazard_ids,
                                                        requested_by=requested_by), dedupe_key=str(row["id"]))  # fmt: skip
    return plans.summary_out(row), created


class Handler:
    def __init__(self, runner: PlanRunner, severities: list[str]):
        self.runner = runner
        self.severities = set(severities)

    async def __call__(self, envelope: EventEnvelope) -> None:
        pool = self.runner.deps.pool
        payload = envelope.payload
        if envelope.event_type == PLAN_REQUESTED:
            claimed = await plans.claim_for_planning(pool, str(payload.plan_id))
            if claimed is None:
                log.info("plan_already_claimed", plan_id=str(payload.plan_id))
                return
            await self._guard(claimed["id"], claimed["thread_id"],
                              self.runner.start(claimed["id"], claimed["thread_id"], [str(h) for h in claimed["hazard_ids"]]))
        elif envelope.event_type == PLAN_APPROVED:
            row = await pool.fetchrow(
                """SELECT p.thread_id, a.decision::text, a.decided_by, a.reason FROM plans p
                   JOIN approvals a ON a.plan_id = p.id WHERE p.id = $1""", payload.plan_id)
            if row is None:
                return
            decision = {"decision": row["decision"], "decided_by": row["decided_by"], "reason": row["reason"]}
            await self._guard(str(payload.plan_id), row["thread_id"], self.runner.resume(row["thread_id"], decision))
        elif envelope.event_type == HAZARD_UPDATED and payload.change in ("created", "updated"):
            if payload.is_drill or (payload.severity in self.severities and payload.hazard_type in AUTO_PLAN_TYPES):
                summary, created = await request_plan(
                    pool, trigger_kind="drill" if payload.is_drill else "hazard", trigger_ref=str(payload.hazard_id),
                    hazard_ids=[str(payload.hazard_id)], requested_by="system",
                    idempotency_key=f"auto:{payload.hazard_id}")  # fmt: skip
                if created:
                    log.info("plan_auto_requested", plan_id=summary["id"], hazard_id=str(payload.hazard_id))

    async def _guard(self, plan_id: str, thread_id: str, run) -> None:
        """A crashing plan must not crash the worker or be retried forever: record it and move on."""
        try:
            await run
        except Exception as exc:
            log.exception("plan_crashed", plan_id=plan_id)
            pool = self.runner.deps.pool
            await Audit(pool, plan_id, thread_id)("supervisor", "error", f"Plan stopped by an internal error: "
                                                  f"{type(exc).__name__}: {exc}"[:500])
            await plans.set_status(pool, plan_id, "failed", unreachable_reason=f"internal error: {type(exc).__name__}")


async def main() -> None:
    settings = get_brain_settings()
    configure_logging(settings.log_level)
    pool = await get_pool()
    async with httpx.AsyncClient(follow_redirects=True) as http, \
            AsyncPostgresSaver.from_conn_string(settings.database_url) as saver:
        await saver.setup()
        deps = Deps(pool=pool, valhalla=ValhallaClient(settings.valhalla_url, http), llm=await build_llm(settings, http),
                    messenger=Messenger(settings, http), settings=settings)  # fmt: skip
        runner = PlanRunner(deps, saver)
        log.info("brain_worker_ready", llm=deps.llm.backend.model if deps.llm else None,
                 twilio=deps.messenger.configured)  # fmt: skip
        try:
            await bus.consume([PLAN_REQUESTED, PLAN_APPROVED, HAZARD_UPDATED], GROUP_BRAIN,
                              f"brain-{socket.gethostname()}", Handler(runner, settings.auto_plan_severities))  # fmt: skip
        finally:
            await close_pool()
            await bus.close_redis()


if __name__ == "__main__":
    asyncio.run(main())
