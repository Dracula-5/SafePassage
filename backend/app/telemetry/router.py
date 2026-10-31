"""POST /api/vehicles (register, patch 001), POST /api/telemetry (real phone GPS) and revocation.

Telemetry is a batch so the PWA can flush its offline queue; (vehicle_id, recorded_at) is
unique, so a resent batch is counted as duplicates and never double-emitted. Each newly
stored fix is published as vehicle.telemetry, oldest first. "Offline" is not stored: it is
derived by v_vehicle_status from last_seen_at.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, field_validator
from reliefroute_contracts.events import LonLat, VehicleTelemetry
from reliefroute_contracts.streams import VEHICLE_TELEMETRY

from app.core import bus
from app.core.auth import require_enrolment, require_operator
from app.core.db import get_pool
from app.core.validation import Name

from .tokens import hash_token, new_token

router = APIRouter(tags=["senses"])
MAX_CLOCK_SKEW = timedelta(minutes=5)   # reject fixes stamped in the future beyond this
MAX_FIX_AGE = timedelta(days=2)         # offline queues older than this are not replayed
MAX_ACCURACY_M = 100_000                # also keeps Infinity, which plain JSON cannot carry, out of the table


class VehicleRegistration(BaseModel):
    label: Name = Field(min_length=1, max_length=100)
    driver_name: Name | None = Field(default=None, max_length=100)
    driver_phone: str | None = Field(default=None, max_length=20, pattern=r"^\+[1-9]\d{6,14}$")
    driver_language: str = Field(min_length=2, max_length=10, pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]+)?$")
    capacity_kg: float = Field(gt=0, le=60_000)
    home_facility_id: UUID | None = None


VEHICLE_SELECT = """SELECT id, label, driver_name, driver_language, capacity_kg, status::text, last_seen_at,
       seconds_since_seen, is_offline, ST_AsGeoJSON(last_position, 6) AS geojson FROM v_vehicle_status"""


def vehicle_out(r) -> dict:
    return {
        "id": str(r["id"]), "label": r["label"], "driver_name": r["driver_name"], "driver_language": r["driver_language"],
        "capacity_kg": r["capacity_kg"], "status": r["status"],
        "position": None if r["geojson"] is None else json.loads(r["geojson"]),
        "last_seen_at": r["last_seen_at"].isoformat() if r["last_seen_at"] else None,
        "seconds_since_seen": r["seconds_since_seen"], "is_offline": r["is_offline"],
    }  # fmt: skip


@router.post("/api/vehicles", status_code=201, dependencies=[Depends(require_enrolment)])
async def register_vehicle(body: VehicleRegistration) -> dict:
    token, digest = new_token()
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        if body.home_facility_id and not await conn.fetchval("SELECT 1 FROM facilities WHERE id = $1", body.home_facility_id):
            raise HTTPException(422, "home_facility_id does not exist")
        vehicle_id = await conn.fetchval(
            """INSERT INTO vehicles (label, driver_name, driver_phone, driver_language, capacity_kg, home_facility_id,
                                     device_token_hash) VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id""",
            body.label, body.driver_name, body.driver_phone, body.driver_language, body.capacity_kg,
            body.home_facility_id, digest,
        )  # fmt: skip
        row = await conn.fetchrow(VEHICLE_SELECT + " WHERE id = $1", vehicle_id)
    return {"vehicle": vehicle_out(row), "device_token": token}


@router.post("/api/vehicles/{vehicle_id}/revoke", dependencies=[Depends(require_operator)])
async def revoke_vehicle(vehicle_id: UUID) -> dict:
    """A lost phone or a registration nobody recognises: its token stops working and planning skips
    it. A route it was driving goes quiet, so the monitor treats it as offline and replans."""
    revoked = await (await get_pool()).fetchval(
        """UPDATE vehicles SET device_token_hash = NULL, status = 'maintenance', updated_at = now()
           WHERE id = $1 RETURNING id""", vehicle_id)
    if revoked is None:
        raise HTTPException(404, "vehicle not found")
    return {"id": str(vehicle_id), "status": "maintenance"}


class TelemetryFix(BaseModel):
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
    recorded_at: datetime
    speed_mps: float | None = Field(default=None, ge=0, le=100)
    heading_deg: float | None = Field(default=None, ge=0, lt=360)
    accuracy_m: float | None = Field(default=None, ge=0, le=MAX_ACCURACY_M)

    @field_validator("recorded_at")
    @classmethod
    def _sane_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("recorded_at must include a timezone")
        now = datetime.now(UTC)
        if value > now + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at is in the future")
        if value < now - MAX_FIX_AGE:
            raise ValueError("recorded_at is too old to replay")
        return value


class TelemetryBatch(BaseModel):
    vehicle_id: UUID
    fixes: list[TelemetryFix] = Field(min_length=1, max_length=500)


@router.post("/api/telemetry", status_code=202)
async def post_telemetry(batch: TelemetryBatch, x_device_token: Annotated[str, Header()]) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        owner = await conn.fetchval("SELECT id FROM vehicles WHERE device_token_hash = $1", hash_token(x_device_token))
        if owner is None or owner != batch.vehicle_id:
            raise HTTPException(401, "device token does not belong to this vehicle")
        stored = []
        for fix in sorted(batch.fixes, key=lambda f: f.recorded_at):
            row = await conn.fetchrow(
                """INSERT INTO telemetry (vehicle_id, geom, speed_mps, heading_deg, accuracy_m, recorded_at)
                   VALUES ($1, ST_SetSRID(ST_MakePoint($2, $3), 4326), $4, $5, $6, $7)
                   ON CONFLICT (vehicle_id, recorded_at) DO NOTHING RETURNING id""",
                batch.vehicle_id, fix.lon, fix.lat, fix.speed_mps, fix.heading_deg, fix.accuracy_m, fix.recorded_at,
            )  # fmt: skip
            if row:
                stored.append((row["id"], fix))
        if stored:
            _, latest = stored[-1]
            await conn.execute(
                """UPDATE vehicles SET last_position = ST_SetSRID(ST_MakePoint($2, $3), 4326), last_seen_at = $4,
                       updated_at = now()
                   WHERE id = $1 AND (last_seen_at IS NULL OR last_seen_at < $4)""",
                batch.vehicle_id, latest.lon, latest.lat, latest.recorded_at,
            )  # fmt: skip
    for telemetry_id, fix in stored:
        await bus.try_publish(VEHICLE_TELEMETRY, VehicleTelemetry(
            telemetry_id=telemetry_id, vehicle_id=batch.vehicle_id, position=LonLat(lon=fix.lon, lat=fix.lat),
            recorded_at=fix.recorded_at, speed_mps=fix.speed_mps, heading_deg=fix.heading_deg, accuracy_m=fix.accuracy_m,
        ), dedupe_key=f"{batch.vehicle_id}:{fix.recorded_at.isoformat()}")  # fmt: skip
    return {"accepted": len(stored), "duplicates": len(batch.fixes) - len(stored)}
