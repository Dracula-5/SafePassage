"""Writes to A-owned plan tables (plans, routes, orders, order_lines, approvals, notifications)."""

from __future__ import annotations

import json
from typing import Any

import asyncpg


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
           RETURNING id::text, thread_id, hazard_ids::text[], is_drill""",
        plan_id,
    )


async def create_plan(pool: asyncpg.Pool, *, trigger_kind: str, trigger_ref: str | None, hazard_ids: list[str],
                      requested_by: str, idempotency_key: str | None) -> tuple[asyncpg.Record, bool]:
    """Insert a pending plan. With an idempotency key already used, return the original plan (created=False)."""
    if idempotency_key:
        existing = await pool.fetchrow(PLAN_SUMMARY_SQL + " WHERE idempotency_key = $1", idempotency_key)
        if existing:
            return existing, False
    row = await pool.fetchrow(
        """INSERT INTO plans (thread_id, trigger_kind, trigger_ref, hazard_ids, requested_by, idempotency_key, is_drill)
           VALUES (gen_random_uuid()::text, $1, $2, $3::uuid[], $4, $5,
                   EXISTS (SELECT 1 FROM v_active_hazards WHERE id = ANY($3::uuid[]) AND is_drill))
           ON CONFLICT (idempotency_key) DO NOTHING RETURNING id""",
        trigger_kind, trigger_ref, hazard_ids, requested_by, idempotency_key,
    )  # fmt: skip
    if row is None:  # lost a race on the same idempotency key
        return await pool.fetchrow(PLAN_SUMMARY_SQL + " WHERE idempotency_key = $1", idempotency_key), False
    return await pool.fetchrow(PLAN_SUMMARY_SQL + " WHERE id = $1", row["id"]), True


PLAN_SUMMARY_SQL = """SELECT id, parent_plan_id, thread_id, status::text, trigger_kind::text, hazard_ids, is_drill,
       requires_approval, summary, created_at, updated_at FROM plans"""


def summary_out(r: asyncpg.Record) -> dict:
    return {"id": str(r["id"]), "parent_plan_id": str(r["parent_plan_id"]) if r["parent_plan_id"] else None,
            "status": r["status"], "trigger_kind": r["trigger_kind"], "hazard_ids": [str(h) for h in r["hazard_ids"]],
            "is_drill": r["is_drill"], "requires_approval": r["requires_approval"], "summary": r["summary"],
            "created_at": r["created_at"].isoformat(), "updated_at": r["updated_at"].isoformat()}
