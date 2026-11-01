"""A duty officer asks one particular driver to go to one place.

Plans choose trucks by themselves. This is for when a person knows better: they pick a place, see who is
nearest, and ask that driver, who answers on their screen. Nothing goes out by SMS, which needs a paid
number. An accepted request puts that truck first in the next plan for that place (agents/data.py).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.core.auth import require_operator
from app.core.db import get_pool
from app.core.sessions import account_for

from .access import require_vehicle_access

router = APIRouter(tags=["senses"])
REQUEST_SELECT = """SELECT q.id, q.vehicle_id, q.hazard_id, q.status, q.requested_by, q.created_at, q.answered_at, h.headline AS place
                    FROM driver_requests q JOIN hazards h ON h.id = q.hazard_id"""


def request_out(r) -> dict:
    return {"id": str(r["id"]), "vehicle_id": str(r["vehicle_id"]), "hazard_id": str(r["hazard_id"]), "place": r["place"],
            "status": r["status"], "requested_by": r["requested_by"], "created_at": r["created_at"].isoformat(),
            "answered_at": r["answered_at"].isoformat() if r["answered_at"] else None}  # fmt: skip


class NewRequest(BaseModel):
    vehicle_id: UUID
    hazard_id: UUID


@router.post("/api/driver-requests", status_code=201, dependencies=[Depends(require_operator)])
async def send_request(body: NewRequest, request: Request) -> dict:
    account = await account_for(request)
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        if not await conn.fetchval("SELECT 1 FROM v_active_hazards WHERE id = $1", body.hazard_id):
            raise HTTPException(422, "that alert or drill has ended")
        truck = await conn.fetchrow("SELECT status::text, is_offline FROM v_vehicle_status WHERE id = $1", body.vehicle_id)
        if truck is None or truck["status"] == "maintenance":
            raise HTTPException(404, "that truck is not on the fleet")
        if truck["is_offline"]:
            raise HTTPException(409, "this driver is offline, so the request would not reach them")
        if await conn.fetchval("SELECT 1 FROM v_vehicle_assignment WHERE vehicle_id = $1", body.vehicle_id):
            raise HTTPException(409, "this driver is on a route: they cannot leave their goods")
        try:
            request_id = await conn.fetchval(
                "INSERT INTO driver_requests (vehicle_id, hazard_id, requested_by) VALUES ($1, $2, $3) RETURNING id",
                body.vehicle_id, body.hazard_id, account["name"] if account else "duty officer")  # fmt: skip
        except asyncpg.UniqueViolationError as exc:
            raise HTTPException(409, "this driver has not answered your last request yet") from exc
        return request_out(await conn.fetchrow(REQUEST_SELECT + " WHERE q.id = $1", request_id))


@router.get("/api/driver-requests", dependencies=[Depends(require_operator)])
async def recent_requests() -> list[dict]:
    rows = await (await get_pool()).fetch(REQUEST_SELECT + " WHERE q.created_at > now() - interval '1 day' ORDER BY q.created_at DESC")
    return [request_out(r) for r in rows]


@router.delete("/api/driver-requests/{request_id}", status_code=204, dependencies=[Depends(require_operator)])
async def withdraw_request(request_id: UUID) -> None:
    await (await get_pool()).execute("UPDATE driver_requests SET status = 'withdrawn' WHERE id = $1 AND status = 'sent'", request_id)


@router.get("/api/hazards/{hazard_id}/distances", dependencies=[Depends(require_operator)])
async def distances_to(hazard_id: UUID, limit: Annotated[int, Query(ge=1, le=500)] = 200) -> dict[str, float]:
    """How far each truck is from a place, in metres, nearest first. Inside the zone counts as 0."""
    rows = await (await get_pool()).fetch(
        """SELECT v.id, ST_Distance(v.last_position::geography, h.geom::geography) AS metres
           FROM v_vehicle_status v, hazards h
           WHERE h.id = $1 AND v.last_position IS NOT NULL AND v.status <> 'maintenance'
           ORDER BY metres LIMIT $2""", hazard_id, limit)  # fmt: skip
    return {str(r["id"]): r["metres"] for r in rows}


@router.get("/api/vehicles/{vehicle_id}/request", dependencies=[Depends(require_vehicle_access)])
async def current_request(vehicle_id: UUID) -> dict | None:
    row = await (await get_pool()).fetchrow(
        REQUEST_SELECT + """ WHERE q.vehicle_id = $1 AND q.status IN ('sent', 'accepted')
                               AND EXISTS (SELECT 1 FROM v_active_hazards a WHERE a.id = q.hazard_id)
                             ORDER BY q.created_at DESC LIMIT 1""", vehicle_id)  # fmt: skip
    return request_out(row) if row else None


class Answer(BaseModel):
    accept: bool


@router.post("/api/vehicles/{vehicle_id}/request/answer", dependencies=[Depends(require_vehicle_access)])
async def answer_request(vehicle_id: UUID, body: Answer) -> dict:
    status = "accepted" if body.accept else "declined"
    answered = await (await get_pool()).fetchval(
        """UPDATE driver_requests SET status = $2, answered_at = now()
           WHERE vehicle_id = $1 AND status = 'sent' RETURNING id""", vehicle_id, status)  # fmt: skip
    if answered is None:
        raise HTTPException(404, "no request is waiting for an answer")
    return {"id": str(answered), "status": status}
