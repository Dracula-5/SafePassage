"""Instance A REST endpoints (contracts/openapi.yaml, tag `brain`)."""

from __future__ import annotations

import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from reliefroute_contracts.enums import OrderStatus, PlanStatus
from reliefroute_contracts.events import PlanApproved
from reliefroute_contracts.streams import PLAN_APPROVED

from app.core import bus
from app.core.auth import require_operator
from app.core.db import get_pool
from app.core.settings_brain import get_brain_settings
from app.graph import plans

router = APIRouter(tags=["brain"])


class PlanTrigger(BaseModel):
    hazard_ids: list[UUID] = Field(default_factory=list, description="Empty: all active hazards")
    reason: str | None = Field(default=None, max_length=500)
    requested_by: str = Field(min_length=1, max_length=100)


@router.post("/api/plans/trigger", status_code=202, dependencies=[Depends(require_operator)])
async def trigger_plan(body: PlanTrigger, idempotency_key: Annotated[str | None, Header(max_length=200)] = None) -> dict:
    pool = await get_pool()
    hazard_ids = [str(h) for h in body.hazard_ids] or [
        str(r["id"]) for r in await pool.fetch("SELECT id FROM v_active_hazards ORDER BY id")]
    if not hazard_ids:
        raise HTTPException(422, "no active hazards to plan for")
    active = await pool.fetchval("SELECT count(*) FROM v_active_hazards WHERE id = ANY($1::uuid[])", hazard_ids)
    if active != len(hazard_ids):
        raise HTTPException(422, "some hazard_ids are not active hazards")
    summary, _ = await plans.request_plan(pool, trigger_kind="manual", trigger_ref=body.reason, hazard_ids=hazard_ids,
                                          requested_by=body.requested_by,
                                          idempotency_key=f"manual:{idempotency_key}" if idempotency_key else None)  # fmt: skip
    return summary


@router.get("/api/plans")
async def list_plans(status: PlanStatus | None = None, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[dict]:
    rows = await (await get_pool()).fetch(
        plans.PLAN_SUMMARY_SQL + " WHERE ($1::plan_status IS NULL OR status = $1) ORDER BY created_at DESC LIMIT $2",
        status.value if status else None, limit)
    return [plans.summary_out(r) for r in rows]


async def _orders(pool, plan_id: UUID | None = None, status: str | None = None) -> list[dict]:
    rows = await pool.fetch(
        """SELECT o.*, o.status::text AS status_text,
                  COALESCE(json_agg(json_build_object('item_code', l.item_code, 'quantity', l.quantity))
                           FILTER (WHERE l.order_id IS NOT NULL), '[]') AS lines
           FROM orders o LEFT JOIN order_lines l ON l.order_id = o.id
           WHERE ($1::uuid IS NULL OR o.plan_id = $1) AND ($2::order_status IS NULL OR o.status = $2)
           GROUP BY o.id ORDER BY o.created_at""",
        plan_id, status,
    )
    return [{
        "id": str(r["id"]), "plan_id": str(r["plan_id"]), "route_id": str(r["route_id"]) if r["route_id"] else None,
        "supplier_id": str(r["supplier_id"]), "destination_facility_id": str(r["destination_facility_id"]),
        "status": r["status_text"], "idempotency_key": r["idempotency_key"],
        "lines": r["lines"] if isinstance(r["lines"], list) else json.loads(r["lines"]),
        "dispatched_at": r["dispatched_at"].isoformat() if r["dispatched_at"] else None,
        "delivered_at": r["delivered_at"].isoformat() if r["delivered_at"] else None,
        "created_at": r["created_at"].isoformat(),
    } for r in rows]  # fmt: skip


def _route_out(r) -> dict:
    return {"id": str(r["id"]), "vehicle_id": str(r["vehicle_id"]), "status": r["status"],
            "geometry": json.loads(r["geojson"]), "distance_m": r["distance_m"], "duration_s": r["duration_s"],
            "load_kg": r["load_kg"], "min_hazard_clearance_m": r["min_hazard_clearance_m"], "stops": r["stops"],
            "completed_stops": r["completed_stops"]}


ROUTE_SQL = """SELECT id, vehicle_id, status::text, ST_AsGeoJSON(geom, 6) AS geojson, distance_m, duration_s, load_kg,
                      min_hazard_clearance_m, stops, completed_stops, instructions FROM routes"""


@router.get("/api/plans/{plan_id}")
async def get_plan(plan_id: UUID) -> dict:
    pool = await get_pool()
    row = await pool.fetchrow(plans.PLAN_SUMMARY_SQL + " WHERE id = $1", plan_id)
    if row is None:
        raise HTTPException(404, "plan not found")
    extra = await pool.fetchrow("SELECT demand, unreachable_reason, escalations, triage FROM plans WHERE id = $1", plan_id)
    routes = await pool.fetch(ROUTE_SQL + " WHERE plan_id = $1 ORDER BY created_at", plan_id)
    critic = await pool.fetch(
        """SELECT payload, created_at FROM agent_events WHERE plan_id = $1 AND node = 'critic'
             AND event_type IN ('decision', 'critic_rejection') ORDER BY id""", plan_id)
    approval = await pool.fetchrow("SELECT * FROM approvals WHERE plan_id = $1", plan_id)
    return plans.summary_out(row) | {
        "demand": [{k: v for k, v in line.items()} for line in (extra["demand"] or {}).get("lines", [])],
        "routes": [_route_out(r) for r in routes],
        "orders": await _orders(pool, plan_id),
        "critic": [{"attempt": c["payload"]["attempt"], "passed": c["payload"]["passed"],
                    "violations": c["payload"]["violations"], "created_at": c["created_at"].isoformat()} for c in critic],
        "approval": None if approval is None else {
            "id": str(approval["id"]), "plan_id": str(approval["plan_id"]), "decision": str(approval["decision"]),
            "decided_by": approval["decided_by"], "reason": approval["reason"],
            "decided_at": approval["decided_at"].isoformat()},
        "unreachable_reason": extra["unreachable_reason"],
        "escalations": extra["escalations"],
        "triage": extra["triage"],
    }  # fmt: skip


class ApprovalRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    decided_by: str = Field(min_length=1, max_length=100)
    reason: str | None = Field(default=None, max_length=1000)


async def _close_expired(conn, plan_id: UUID, decided_by: str) -> None:
    reason = "every hazard of this plan ended before approval"
    await conn.execute("""UPDATE plans SET status = 'superseded', unreachable_reason = $2, updated_at = now()
                          WHERE id = $1""", plan_id, reason)
    await conn.execute(
        """INSERT INTO agent_events (plan_id, thread_id, node, event_type, message, payload)
           SELECT id, thread_id, 'human', 'approval_decided', $2, $3 FROM plans WHERE id = $1""",
        plan_id, f"Approval by {decided_by} refused: {reason}", {"decided_by": decided_by})  # fmt: skip


@router.post("/api/plans/{plan_id}/approve", dependencies=[Depends(require_operator)])
async def decide_plan(plan_id: UUID, body: ApprovalRequest) -> dict:
    pool = await get_pool()
    expired = False
    async with pool.acquire() as conn, conn.transaction():
        status = await conn.fetchval("SELECT status::text FROM plans WHERE id = $1 FOR UPDATE", plan_id)
        if status is None:
            raise HTTPException(404, "plan not found")
        if await conn.fetchval("SELECT 1 FROM approvals WHERE plan_id = $1", plan_id):
            raise HTTPException(409, "this plan already has a decision")
        if status != "awaiting_approval":
            raise HTTPException(409, f"plan is {status}, not awaiting approval")
        expired = body.decision == "approved" and await conn.fetchval(
            """SELECT cardinality(p.hazard_ids) > 0
                      AND NOT EXISTS (SELECT 1 FROM v_active_hazards h WHERE h.id = ANY(p.hazard_ids))
               FROM plans p WHERE p.id = $1""", plan_id)  # fmt: skip
        if expired:
            await _close_expired(conn, plan_id, body.decided_by)
        else:
            row = await conn.fetchrow(
                """INSERT INTO approvals (plan_id, decision, decided_by, reason) VALUES ($1, $2, $3, $4) RETURNING *""",
                plan_id, body.decision, body.decided_by, body.reason)
            await conn.execute("UPDATE plans SET status = $2::plan_status, updated_at = now() WHERE id = $1",
                               plan_id, body.decision)  # fmt: skip
            thread_id = await conn.fetchval("SELECT thread_id FROM plans WHERE id = $1", plan_id)
            await conn.execute(
                """INSERT INTO agent_events (plan_id, thread_id, node, event_type, message, payload)
                   VALUES ($1, $2, 'human', 'approval_decided', $3, $4)""",
                plan_id, thread_id,
                f"{body.decision.upper()} by {body.decided_by}" + (f": {body.reason}" if body.reason else ""),
                {"decision": body.decision, "decided_by": body.decided_by},
            )  # fmt: skip
    if expired:  # raised here so the transaction above commits the closed plan
        raise HTTPException(409, "every hazard of this plan has ended; it was closed without dispatching")
    # The paused graph resumes on plan.approved. A rejection ends the plan here; the paused
    # thread is simply never resumed.
    if body.decision == "approved":
        await bus.try_publish(PLAN_APPROVED, PlanApproved(plan_id=plan_id, approval_id=row["id"], decided_by=body.decided_by,
                                                          decided_at=row["decided_at"]), dedupe_key=str(plan_id))  # fmt: skip
    return {"id": str(row["id"]), "plan_id": str(plan_id), "decision": body.decision, "decided_by": body.decided_by,
            "reason": body.reason, "decided_at": row["decided_at"].isoformat()}


@router.get("/api/orders")
async def list_orders(plan_id: UUID | None = None, status: OrderStatus | None = None) -> list[dict]:
    return await _orders(await get_pool(), plan_id, status.value if status else None)


@router.get("/api/vehicles/{vehicle_id}/assignment")
async def get_vehicle_assignment(vehicle_id: UUID) -> dict:
    pool = await get_pool()
    a = await pool.fetchrow("SELECT route_id, plan_id, is_drill FROM v_vehicle_assignment WHERE vehicle_id = $1", vehicle_id)
    if a is None:
        raise HTTPException(404, "no current assignment for this vehicle")
    route = await pool.fetchrow(ROUTE_SQL + " WHERE id = $1", a["route_id"])
    instructions = route["instructions"] or {}
    return {"vehicle_id": str(vehicle_id), "plan_id": str(a["plan_id"]), "is_drill": a["is_drill"],
            "route": _route_out(route),
            "instructions": {"language": instructions.get("language", "en"), "text": instructions.get("text", ""),
                             "audio_url": None}}  # fmt: skip


@router.get("/api/brain/health")
async def brain_health() -> list[dict]:
    """A-side dependencies, in the SourceHealth shape. Reports configuration and a live Valhalla probe."""
    import httpx

    from app.routing.valhalla import ValhallaClient

    settings = get_brain_settings()
    out = []
    async with httpx.AsyncClient() as http:
        try:
            await ValhallaClient(settings.valhalla_url, http, retries=0, timeout_s=5).status()
            valhalla_ok, error = True, None
        except Exception as exc:  # report, never fail the health endpoint
            valhalla_ok, error = False, f"{type(exc).__name__}: {exc}"[:200]
    out.append({"source": "valhalla", "breaker_state": "closed", "last_success_at": None, "last_failure_at": None,
                "last_error": error, "ttl_seconds": 60, "fallback_source": None, "is_stale": not valhalla_ok})
    out.append({"source": "llm", "breaker_state": "closed", "last_success_at": None, "last_failure_at": None,
                "last_error": None if settings.gemini_api_key else "GEMINI_API_KEY not set (rule-based fallbacks in use)",
                "ttl_seconds": 60, "fallback_source": None if settings.gemini_api_key else "rule_based",
                "is_stale": not settings.gemini_api_key})
    twilio = bool(settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_from_number)
    out.append({"source": "twilio", "breaker_state": "closed", "last_success_at": None, "last_failure_at": None,
                "last_error": None if twilio else "credentials not configured; messages are recorded, not sent",
                "ttl_seconds": 60, "fallback_source": None, "is_stale": not twilio})
    return out
