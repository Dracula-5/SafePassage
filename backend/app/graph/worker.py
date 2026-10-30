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
from reliefroute_contracts.events import EventEnvelope
from reliefroute_contracts.streams import (
    GROUP_BRAIN, HAZARD_UPDATED, PLAN_APPROVED, PLAN_REQUESTED, VEHICLE_DEVIATION, VEHICLE_TELEMETRY,
)

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
from .monitor import RouteMonitor
from .nodes import Deps

log = get_logger(__name__)
GROUP_MONITOR = f"{GROUP_BRAIN}-monitor"  # separate group: plan runs must not hold up telemetry
CRASHED_AFTER_S = 60       # matches the bus's re-claim delay
REQUEUE_EVERY_S = 30
AUTO_PLAN_TYPES = {"flood", "cyclone", "heavy_rain", "landslide", "tsunami", "earthquake"}


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
                # Redelivered because a worker died mid-run? Events run one at a time, so this
                # process is not working on it. Otherwise it is an ordinary duplicate.
                thread_id = await plans.abandoned_thread(pool, str(payload.plan_id), CRASHED_AFTER_S)
                if thread_id:
                    log.warning("plan_resumed_after_crash", plan_id=str(payload.plan_id))
                    await self._guard(str(payload.plan_id), thread_id, self.runner.continue_after_crash(thread_id))
                else:
                    log.info("plan_already_claimed", plan_id=str(payload.plan_id))
                return
            hazard_ids = [str(h) for h in claimed["hazard_ids"]]
            replacing_route = claimed["trigger_kind"] == "replan" and claimed["trigger_ref"]
            run = (self.runner.start_replan(claimed["id"], claimed["thread_id"], hazard_ids, claimed["parent_plan_id"],
                                            claimed["trigger_ref"]) if replacing_route
                   else self.runner.start(claimed["id"], claimed["thread_id"], hazard_ids))  # fmt: skip
            await self._guard(claimed["id"], claimed["thread_id"], run)
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
                summary, created = await plans.request_plan(
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


async def requeue_loop(pool) -> None:
    while True:
        try:
            if count := await plans.requeue_stuck_plans(pool, CRASHED_AFTER_S):
                log.warning("plans_requeued", count=count)
        except Exception:  # Redis may still be down: try again next round
            log.exception("requeue_failed")
        await asyncio.sleep(REQUEUE_EVERY_S)


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
        monitor = RouteMonitor(deps)
        consumer = f"brain-{socket.gethostname()}"
        try:
            await asyncio.gather(
                bus.consume([PLAN_REQUESTED, PLAN_APPROVED, HAZARD_UPDATED], GROUP_BRAIN, consumer,
                            Handler(runner, settings.auto_plan_severities)),
                bus.consume([VEHICLE_TELEMETRY, HAZARD_UPDATED, VEHICLE_DEVIATION], GROUP_MONITOR, consumer, monitor.handle),
                monitor.run_sweeps(),
                requeue_loop(pool),
            )  # fmt: skip
        finally:
            await close_pool()
            await bus.close_redis()


if __name__ == "__main__":
    # psycopg's async mode needs a selector loop; Windows defaults to the proactor loop.
    asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
