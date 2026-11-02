"""Simulated drivers for presentations. All of this is off unless DEMO_FLEET is true.

backend/tests/tools/demo_fleet.py drives these trucks from a container of its own (compose profile
`demo`, never part of the image) and reports through POST /api/telemetry, exactly as a phone does, so
everything downstream runs for real. What keeps it honest: each label starts with TEST-SIM-GPS, every
screen marks the truck as simulated, a plan using one always waits for a person, and the drivers made
here have no phone number, so no message can reach anyone.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import require_operator
from app.core.config import SIMULATED_LABEL, get_common_settings
from app.core.db import get_pool
from app.core.validation import Name

from .router import Driver, VEHICLE_SELECT, VehicleRegistration, _create_vehicle, _own_vehicle, vehicle_out
from .tokens import new_token

router = APIRouter(tags=["senses"])


# Town name: the district's code on a number plate. Where each town is, the simulator knows.
TOWNS = {
    "Srikakulam": "30", "Vizianagaram": "35", "Visakhapatnam": "31", "Kakinada": "05", "Rajamahendravaram": "05", "Eluru": "37",
    "Vijayawada": "16", "Guntur": "07", "Ongole": "27", "Nellore": "26", "Tirupati": "03", "Kadapa": "04", "Kurnool": "21",
    "Anantapur": "02",
}  # fmt: skip


def demo_on() -> None:
    if not get_common_settings().demo_fleet:
        raise HTTPException(404, "the demo fleet is off")


def _check_town(name: str) -> None:
    if name not in TOWNS:
        raise HTTPException(422, f"unknown town: choose one of {', '.join(TOWNS)}")


@router.get("/api/demo")
async def demo_status() -> dict:
    """Public, so the screens know whether to say that the trucks are simulated."""
    if not get_common_settings().demo_fleet:
        return {"enabled": False}
    trucks = await (await get_pool()).fetchval(
        "SELECT count(*) FROM demo_drivers d JOIN vehicles v ON v.id = d.vehicle_id WHERE v.status <> 'maintenance'")
    return {"enabled": True, "trucks": trucks, "towns": [{"name": name, "rto": rto} for name, rto in TOWNS.items()]}


@router.get("/api/demo/fleet", dependencies=[Depends(demo_on), Depends(require_operator)])
async def demo_fleet() -> list[dict]:
    """What the simulator drives. Removed trucks stay listed, so it does not create them again."""
    rows = await (await get_pool()).fetch(
        """SELECT d.vehicle_id, d.device_token, d.town, d.mode, v.label, v.account_id IS NOT NULL AS has_person,
                  v.status = 'maintenance' AS removed, ST_X(v.last_position) AS last_lon, ST_Y(v.last_position) AS last_lat,
                  a.route_id, q.id AS request_id, q.status AS request_status, q.hazard_id AS request_hazard_id
           FROM demo_drivers d JOIN vehicles v ON v.id = d.vehicle_id
           LEFT JOIN v_vehicle_assignment a ON a.vehicle_id = d.vehicle_id
           LEFT JOIN LATERAL (SELECT r.id, r.status, r.hazard_id FROM driver_requests r
                              WHERE r.vehicle_id = d.vehicle_id AND r.status IN ('sent', 'accepted')
                              ORDER BY r.created_at DESC LIMIT 1) q ON true
           ORDER BY v.label""")  # fmt: skip
    ids = ("vehicle_id", "route_id", "request_id", "request_hazard_id")
    return [dict(r) | {name: str(r[name]) if r[name] else None for name in ids} for r in rows]


class SimulatedDriver(BaseModel):
    driver_name: Name = Field(min_length=2, max_length=100)
    plate: Name = Field(min_length=2, max_length=40)
    capacity_kg: float = Field(gt=0, le=60_000)
    driver_language: str = Field(default="te", min_length=2, max_length=10, pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]+)?$")
    town: str = Field(max_length=40)


async def _adopt(conn, vehicle_id: str | UUID, token: str, town: str) -> None:
    await conn.execute("INSERT INTO demo_drivers (vehicle_id, device_token, town) VALUES ($1, $2, $3)", vehicle_id, token, town)


@router.post("/api/demo/drivers", status_code=201, dependencies=[Depends(demo_on), Depends(require_operator)])
async def add_simulated_driver(body: SimulatedDriver) -> dict:
    _check_town(body.town)
    created = await _create_vehicle(VehicleRegistration(
        label=f"{SIMULATED_LABEL} {body.plate}", driver_name=body.driver_name, driver_language=body.driver_language,
        capacity_kg=body.capacity_kg))  # fmt: skip
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _adopt(conn, created["vehicle"]["id"], created["device_token"], body.town)
        return vehicle_out(await conn.fetchrow(VEHICLE_SELECT + " WHERE id = $1", UUID(created["vehicle"]["id"])))


class MyTruck(BaseModel):
    town: str = Field(max_length=40)
    capacity_kg: float = Field(gt=0, le=60_000)


@router.post("/api/demo/my-truck", dependencies=[Depends(demo_on)])
async def simulate_my_truck(body: MyTruck, account: Driver) -> dict:
    """For a driver presenting from outside Andhra Pradesh, where their phone's GPS can't be routed."""
    _check_town(body.town)
    row = await _own_vehicle(account)
    if row is None:
        raise HTTPException(404, "no vehicle registered yet")
    if row["simulated"]:
        raise HTTPException(409, "this truck is already simulated")
    token, digest = new_token()  # the phone's token stops working, so only one thing reports for this truck
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """UPDATE vehicles SET label = $2 || ' ' || label, capacity_kg = $3, device_token_hash = $4, updated_at = now()
               WHERE id = $1""", row["id"], SIMULATED_LABEL, body.capacity_kg, digest)  # fmt: skip
        await _adopt(conn, row["id"], token, body.town)
        return vehicle_out(await conn.fetchrow(VEHICLE_SELECT + " WHERE id = $1", row["id"]))


class DriverMode(BaseModel):
    mode: Literal["normal", "gps_lost", "off_route"]


@router.post("/api/demo/drivers/{vehicle_id}/mode", dependencies=[Depends(demo_on), Depends(require_operator)])
async def set_driver_mode(vehicle_id: UUID, body: DriverMode) -> dict:
    """Makes a truck misbehave on purpose, so the closed loop can be shown without a real breakdown."""
    found = await (await get_pool()).fetchval(
        "UPDATE demo_drivers SET mode = $2 WHERE vehicle_id = $1 RETURNING vehicle_id", vehicle_id, body.mode)
    if found is None:
        raise HTTPException(404, "no simulated driver with that id")
    return {"vehicle_id": str(vehicle_id), "mode": body.mode}


@router.post("/api/demo/restock", status_code=204, dependencies=[Depends(demo_on), Depends(require_operator)])
async def restock_depots() -> None:
    """What a real supplier does after a delivery: a fresh report. Delivered goods stop counting as reserved
    from then on (v_available_stock), and without this a few rehearsals would empty the simulated depots."""
    await (await get_pool()).execute(
        """UPDATE stock SET reported_at = now(), updated_at = now()
           WHERE supplier_id IN (SELECT id FROM suppliers WHERE name LIKE 'TEST-SIM%')""")


@router.delete("/api/demo", status_code=204, dependencies=[Depends(demo_on), Depends(require_operator)])
async def end_demo() -> None:
    """Before going live: takes simulated trucks off the fleet and simulated stock off the shelves. A person's own
    truck goes back to being theirs. Stop the simulator first, or it creates its fleet again."""
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("""UPDATE vehicles SET label = substr(label, length($1) + 2), device_token_hash = NULL, updated_at = now()
                              WHERE account_id IS NOT NULL AND label LIKE $1 || ' %'""", SIMULATED_LABEL)  # fmt: skip
        await conn.execute("""UPDATE vehicles SET device_token_hash = NULL, status = 'maintenance', updated_at = now()
                              WHERE label LIKE $1 || ' %'""", SIMULATED_LABEL)  # fmt: skip
        await conn.execute("DELETE FROM stock WHERE supplier_id IN (SELECT id FROM suppliers WHERE name LIKE 'TEST-SIM%')")
        # Past orders keep these rows. Renamed, because the simulator only stocks a depot it creates itself.
        for table in ("suppliers", "facilities"):
            await conn.execute(f"UPDATE {table} SET name = name || ' (closed)' WHERE name LIKE 'TEST-SIM%' AND name NOT LIKE '%(closed)'")
        await conn.execute("DELETE FROM evacuations WHERE simulated")
        await conn.execute("DELETE FROM demo_drivers")
