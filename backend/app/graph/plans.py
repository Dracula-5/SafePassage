"""Writes to A-owned plan tables (plans, routes, orders, order_lines, approvals, notifications)."""

from __future__ import annotations

import json
from typing import Any

import asyncpg
from reliefroute_contracts.events import PlanApproved, PlanRequested
from reliefroute_contracts.streams import PLAN_APPROVED, PLAN_REQUESTED

from app.core import bus


def _json(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


async def set_status(pool: asyncpg.Pool, plan_id: str, status: str, **fields: Any) -> None:
    cols = ["status = $2::plan_status", "updated_at = now()"]
    values: list[Any] = [plan_id, status]
    for name, value in fields.items():
        if name not in {"summary", "demand", "allocation", "triage", "escalations", "critic_attempts",
                        "unreachable_reason", "requires_approval", "is_drill"}:
            raise ValueError(f"unknown plan field {name}")
        values.append(_json(value) if name in {"demand", "allocation", "triage", "escalations"} else value)
        cols.append(f"{name} = ${len(values)}")
    await pool.execute(f"UPDATE plans SET {', '.join(cols)} WHERE id = $1", *values)


async def claim_for_planning(pool: asyncpg.Pool, plan_id: str) -> asyncpg.Record | None:
    """pending -> planning, exactly once (duplicate plan.requested deliveries are no-ops)."""
    return await pool.fetchrow(
        """UPDATE plans SET status = 'planning', updated_at = now() WHERE id = $1 AND status = 'pending'
           RETURNING id::text, thread_id, hazard_ids::text[], is_drill, parent_plan_id::text, trigger_kind::text,
                     trigger_ref""",
        plan_id,
    )


async def create_plan(pool: asyncpg.Pool, *, trigger_kind: str, trigger_ref: str | None, hazard_ids: list[str],
                      requested_by: str, idempotency_key: str | None,
                      parent_plan_id: str | None = None) -> tuple[asyncpg.Record, bool]:
    """Insert a pending plan. With an idempotency key already used, return the original plan (created=False)."""
    if idempotency_key:
        existing = await pool.fetchrow(PLAN_SUMMARY_SQL + " WHERE idempotency_key = $1", idempotency_key)
        if existing:
            return existing, False
    row = await pool.fetchrow(
        """INSERT INTO plans (thread_id, trigger_kind, trigger_ref, hazard_ids, requested_by, idempotency_key, is_drill,
                              parent_plan_id)
           VALUES (gen_random_uuid()::text, $1, $2, $3::uuid[], $4, $5,
                   EXISTS (SELECT 1 FROM v_active_hazards WHERE id = ANY($3::uuid[]) AND is_drill), $6)
           ON CONFLICT (idempotency_key) DO NOTHING RETURNING id""",
        trigger_kind, trigger_ref, hazard_ids, requested_by, idempotency_key, parent_plan_id,
    )  # fmt: skip
    if row is None:  # lost a race on the same idempotency key
        return await pool.fetchrow(PLAN_SUMMARY_SQL + " WHERE idempotency_key = $1", idempotency_key), False
    return await pool.fetchrow(PLAN_SUMMARY_SQL + " WHERE id = $1", row["id"]), True


async def request_plan(pool: asyncpg.Pool, *, trigger_kind: str, trigger_ref: str | None, hazard_ids: list[str],
                       requested_by: str, idempotency_key: str | None,
                       parent_plan_id: str | None = None) -> tuple[dict, bool]:
    row, created = await create_plan(pool, trigger_kind=trigger_kind, trigger_ref=trigger_ref, hazard_ids=hazard_ids,
                                     requested_by=requested_by, idempotency_key=idempotency_key,
                                     parent_plan_id=parent_plan_id)  # fmt: skip
    if created:
        await bus.try_publish(PLAN_REQUESTED, PlanRequested(
            plan_id=row["id"], trigger_kind=trigger_kind, trigger_ref=trigger_ref, hazard_ids=hazard_ids,
            parent_plan_id=parent_plan_id, requested_by=requested_by), dedupe_key=str(row["id"]))  # fmt: skip
    return summary_out(row), created


async def abandoned_thread(pool: asyncpg.Pool, plan_id: str, idle_s: float) -> str | None:
    """Thread of a plan left mid-run by a worker that died: claimed, but no progress for a while."""
    return await pool.fetchval(
        """SELECT thread_id FROM plans WHERE id = $1 AND status = 'planning'
             AND updated_at < now() - make_interval(secs => $2)""", plan_id, idle_s)  # fmt: skip


async def requeue_stuck_plans(pool: asyncpg.Pool, idle_s: float) -> int:
    """Re-publish events lost while Redis was down. Their handlers are idempotent, so a plan that
    is merely slow is unaffected."""
    pending = await pool.fetch(
        """SELECT id, trigger_kind::text, trigger_ref, hazard_ids, parent_plan_id, requested_by FROM plans
           WHERE status = 'pending' AND updated_at < now() - make_interval(secs => $1)""", idle_s)  # fmt: skip
    approved = await pool.fetch(
        """SELECT a.id AS approval_id, a.plan_id, a.decided_by, a.decided_at FROM approvals a JOIN plans p ON p.id = a.plan_id
           WHERE p.status = 'approved' AND a.decision = 'approved' AND p.updated_at < now() - make_interval(secs => $1)""",
        idle_s)  # fmt: skip
    for r in pending:
        await bus.publish(PLAN_REQUESTED, PlanRequested(
            plan_id=r["id"], trigger_kind=r["trigger_kind"], trigger_ref=r["trigger_ref"], hazard_ids=r["hazard_ids"],
            parent_plan_id=r["parent_plan_id"], requested_by=r["requested_by"]), dedupe_key=str(r["id"]))  # fmt: skip
    for r in approved:
        await bus.publish(PLAN_APPROVED, PlanApproved(plan_id=r["plan_id"], approval_id=r["approval_id"],
                                                      decided_by=r["decided_by"], decided_at=r["decided_at"]),
                          dedupe_key=str(r["plan_id"]))  # fmt: skip
    return len(pending) + len(approved)


PLAN_SUMMARY_SQL = """SELECT id, parent_plan_id, thread_id, status::text, trigger_kind::text, hazard_ids, is_drill,
       requires_approval, summary, created_at, updated_at FROM plans"""


def summary_out(r: asyncpg.Record) -> dict:
    return {"id": str(r["id"]), "parent_plan_id": str(r["parent_plan_id"]) if r["parent_plan_id"] else None,
            "status": r["status"], "trigger_kind": r["trigger_kind"], "hazard_ids": [str(h) for h in r["hazard_ids"]],
            "is_drill": r["is_drill"], "requires_approval": r["requires_approval"], "summary": r["summary"],
            "created_at": r["created_at"].isoformat(), "updated_at": r["updated_at"].isoformat()}
