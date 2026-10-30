"""Applies every migration to a throwaway database on a real PostGIS server.

Needs TEST_DATABASE_URL pointing at a PostGIS-enabled server where the user may CREATE DATABASE,
e.g. postgresql://reliefroute:reliefroute@localhost:5432/postgres (docker compose).
"""

import os
from uuid import uuid4

import asyncpg
import pytest

from app.core.migrate import MigrationError, _key, apply_all, discover

pytestmark = pytest.mark.integration
ADMIN_URL = os.environ.get("TEST_DATABASE_URL")


def test_discover_puts_baseline_first(tmp_path):
    (tmp_path / "201_b.sql").write_text("")
    (tmp_path / "101_a.sql").write_text("")
    files = discover(migrations_dir=tmp_path)
    assert files[0].name == "schema.sql"
    assert [p.name for p in files[1:]] == ["101_a.sql", "201_b.sql"]


def test_discover_rejects_unowned_prefix(tmp_path):
    (tmp_path / "300_x.sql").write_text("")
    with pytest.raises(MigrationError):
        discover(migrations_dir=tmp_path)


@pytest.fixture
async def scratch_db():
    if not ADMIN_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    admin = await asyncpg.connect(ADMIN_URL, timeout=10)
    name = f"rr_test_{uuid4().hex[:8]}"
    await admin.execute(f'CREATE DATABASE "{name}"')
    base, _, _ = ADMIN_URL.rpartition("/")
    conn = await asyncpg.connect(f"{base}/{name}", timeout=10)
    try:
        yield conn
    finally:
        await conn.close()
        await admin.execute(f'DROP DATABASE "{name}"')
        await admin.close()


async def test_migrations_apply_and_are_idempotent(scratch_db):
    first = await apply_all(scratch_db)
    assert first == [_key(p) for p in discover()]
    assert await apply_all(scratch_db) == []
    views = {r["table_name"] for r in await scratch_db.fetch(
        "SELECT table_name FROM information_schema.views WHERE table_schema = 'public'")}
    assert {"v_active_hazards", "v_vehicle_status", "v_vehicle_assignment",
            "v_available_stock", "v_source_freshness"} <= views


async def test_available_stock_subtracts_open_orders(scratch_db):
    """Exercises the view logic with rows created inside the test (not runtime data)."""
    await apply_all(scratch_db)
    async with scratch_db.transaction():
        fac = await scratch_db.fetchval(
            """INSERT INTO facilities (source, source_ref, kind, name, geom, fetched_at)
               VALUES ('portal', 'test:1', 'warehouse', 'Test WH', ST_SetSRID(ST_MakePoint(0, 0), 4326), now())
               RETURNING id""")
        sup = await scratch_db.fetchval(
            "INSERT INTO suppliers (facility_id, name) VALUES ($1, 'Test Supplier') RETURNING id", fac)
        await scratch_db.execute(
            """INSERT INTO stock (supplier_id, item_code, quantity, unit_weight_kg, reported_at, reported_via)
               VALUES ($1, 'WATER_L', 1000, 1, now() - interval '1 hour', 'portal')""", sup)
        plan = await scratch_db.fetchval(
            "INSERT INTO plans (thread_id, trigger_kind, requested_by) VALUES ('t1', 'manual', 'test') RETURNING id")
        for status, qty, key in (("approved", 300, "k1"), ("cancelled", 500, "k2")):
            order = await scratch_db.fetchval(
                """INSERT INTO orders (plan_id, supplier_id, destination_facility_id, status, idempotency_key)
                   VALUES ($1, $2, $3, $4, $5) RETURNING id""", plan, sup, fac, status, key)
            await scratch_db.execute(
                "INSERT INTO order_lines (order_id, item_code, quantity) VALUES ($1, 'WATER_L', $2)", order, qty)
    row = await scratch_db.fetchrow("SELECT quantity, reserved, available FROM v_available_stock")
    assert (row["quantity"], row["reserved"], row["available"]) == (1000, 300, 700)


async def test_drill_check_constraint(scratch_db):
    await apply_all(scratch_db)
    with pytest.raises(asyncpg.CheckViolationError):
        await scratch_db.execute(
            """INSERT INTO hazards (source, source_ref, dedupe_key, hazard_type, severity, headline, geom, fetched_at)
               VALUES ('drill', 'not-prefixed', 'd1', 'flood', 'severe', 'x',
                       ST_Multi(ST_MakeEnvelope(0, 0, 1, 1, 4326)), now())""")
