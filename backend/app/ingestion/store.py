"""Writes for B-owned tables (hazards, facilities, source_health) and the matching events.

Events are published only after the transaction commits, so a consumer that receives
hazard.updated can always read the new row.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
from reliefroute_contracts.events import HazardUpdated
from reliefroute_contracts.streams import HAZARD_UPDATED

from app.core import bus
from app.core.logging import get_logger

from .models import AdminArea, NormalizedFacility, NormalizedHazard, NormalizedPlace

log = get_logger(__name__)

_RETURNING = """id, source::text, hazard_type::text, severity::text, version,
    ST_XMin(geom) AS x0, ST_YMin(geom) AS y0, ST_XMax(geom) AS x1, ST_YMax(geom) AS y1,
    ST_Area(geom::geography) / 1e6 AS area_km2"""


def _event(row: asyncpg.Record, change: str) -> HazardUpdated:
    return HazardUpdated(
        hazard_id=row["id"], change=change, source=row["source"], hazard_type=row["hazard_type"],
        severity=row["severity"], version=row["version"], bbox=(row["x0"], row["y0"], row["x1"], row["y1"]),
        area_km2=row["area_km2"], is_drill=row["source"] == "drill",
    )  # fmt: skip


async def upsert_hazard(conn: asyncpg.Connection, h: NormalizedHazard) -> HazardUpdated | None:
    """Insert or update by dedupe_key. Returns the event to publish, or None if nothing changed."""
    digest = h.content_hash()
    existing = await conn.fetchrow("SELECT id, content_hash, status::text FROM hazards WHERE dedupe_key = $1", h.dedupe_key)
    values = (
        h.source.value, h.source_ref, h.dedupe_key, h.hazard_type.value, h.severity.value, h.status.value, h.headline,
        h.description, h.area_desc, h.geom.wkb, h.effective_at, h.expires_at, h.issued_at, h.raw, digest,
    )
    if existing is None:
        row = await conn.fetchrow(
            f"""INSERT INTO hazards (source, source_ref, dedupe_key, hazard_type, severity, status, headline, description,
                                     area_desc, geom, effective_at, expires_at, issued_at, raw, content_hash, fetched_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, ST_SetSRID(ST_GeomFromWKB($10), 4326), $11, $12, $13, $14,
                        $15, now())
                RETURNING {_RETURNING}""",
            *values,
        )
        return _event(row, "created")
    if existing["content_hash"] == digest:
        await conn.execute("UPDATE hazards SET fetched_at = now() WHERE id = $1", existing["id"])
        return None
    row = await conn.fetchrow(
        f"""UPDATE hazards SET source = $1, source_ref = $2, hazard_type = $4, severity = $5, status = $6, headline = $7,
                description = $8, area_desc = $9, geom = ST_SetSRID(ST_GeomFromWKB($10), 4326), effective_at = $11,
                expires_at = $12, issued_at = $13, raw = $14, content_hash = $15, fetched_at = now(),
                version = version + 1, updated_at = now()
            WHERE dedupe_key = $3
            RETURNING {_RETURNING}""",
        *values,
    )
    change = "cancelled" if h.status.value == "cancelled" else "expired" if h.status.value == "expired" else "updated"
    return _event(row, change)


async def set_hazard_status(conn: asyncpg.Connection, hazard_id: UUID, status: str) -> HazardUpdated | None:
    row = await conn.fetchrow(
        f"""UPDATE hazards SET status = $2::hazard_status, version = version + 1, updated_at = now()
            WHERE id = $1 AND status = 'active' RETURNING {_RETURNING}""",
        hazard_id, status,
    )  # fmt: skip
    return None if row is None else _event(row, status)


async def expire_due_hazards(conn: asyncpg.Connection) -> list[HazardUpdated]:
    rows = await conn.fetch(
        f"""UPDATE hazards SET status = 'expired', version = version + 1, updated_at = now()
            WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= now()
            RETURNING {_RETURNING}"""
    )
    return [_event(r, "expired") for r in rows]


async def publish_hazard_events(events: list[HazardUpdated]) -> None:
    for event in events:
        await bus.publish(HAZARD_UPDATED, event, dedupe_key=f"{event.hazard_id}:{event.version}")


async def upsert_facilities(conn: asyncpg.Connection, facilities: list[NormalizedFacility]) -> int:
    """Bulk upsert on (source, source_ref). Returns the number of rows written."""
    if not facilities:
        return 0
    # Own transaction (a savepoint when nested): the ON COMMIT DROP temp table must live until the INSERT.
    async with conn.transaction():
        await conn.execute(
            """CREATE TEMP TABLE IF NOT EXISTS facility_load (
                   source facility_source, source_ref text, kind facility_kind, name text, wkb bytea,
                   capacity_people integer, attributes text) ON COMMIT DROP"""
        )
        await conn.copy_records_to_table(
            "facility_load",
            records=[(f.source.value, f.source_ref, f.kind.value, f.name, f.geom.wkb, f.capacity_people, json.dumps(f.attributes))
                     for f in facilities],  # fmt: skip
        )
        result = await conn.execute(
            """INSERT INTO facilities (source, source_ref, kind, name, geom, capacity_people, attributes, fetched_at)
               SELECT DISTINCT ON (source, source_ref) source, source_ref, kind, name,
                      ST_SetSRID(ST_GeomFromWKB(wkb), 4326), capacity_people, attributes::jsonb, now()
               FROM facility_load
               ON CONFLICT (source, source_ref) DO UPDATE SET kind = EXCLUDED.kind, name = EXCLUDED.name,
                   geom = EXCLUDED.geom, capacity_people = EXCLUDED.capacity_people, attributes = EXCLUDED.attributes,
                   fetched_at = now(), updated_at = now()"""
        )
        return int(result.split()[-1])

async def record_source_health(
    conn: asyncpg.Connection | asyncpg.Pool,
    source: str,
    ok: bool,
    ttl_seconds: int,
    breaker_state: str,
    error: str | None = None,
    fallback_source: str | None = None,
) -> None:
    now = datetime.now(UTC)
    await conn.execute(
        """INSERT INTO source_health (source, breaker_state, last_success_at, last_failure_at, last_error,
                                      ttl_seconds, fallback_source, updated_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, now())
           ON CONFLICT (source) DO UPDATE SET breaker_state = EXCLUDED.breaker_state,
               last_success_at = COALESCE(EXCLUDED.last_success_at, source_health.last_success_at),
               last_failure_at = COALESCE(EXCLUDED.last_failure_at, source_health.last_failure_at),
               last_error = EXCLUDED.last_error, ttl_seconds = EXCLUDED.ttl_seconds,
               fallback_source = EXCLUDED.fallback_source, updated_at = now()""",
        source, breaker_state, now if ok else None, None if ok else now, error, ttl_seconds, fallback_source,
    )  # fmt: skip
    log.info("source_health", source=source, ok=ok, breaker=breaker_state, error=error, fallback=fallback_source)


async def upsert_places(conn: asyncpg.Connection, places: list[NormalizedPlace]) -> int:
    if not places:
        return 0
    # Own transaction (a savepoint when nested): the ON COMMIT DROP temp table must live until the INSERT.
    async with conn.transaction():
        await conn.execute(
            """CREATE TEMP TABLE IF NOT EXISTS place_load (source text, source_ref text, place_type text, name text,
                   wkb bytea, population integer, population_source text) ON COMMIT DROP"""
        )
        await conn.copy_records_to_table("place_load", records=[
            (p.source, p.source_ref, p.place_type, p.name, p.geom.wkb, p.population, p.population_source) for p in places])
        result = await conn.execute(
            """INSERT INTO places (source, source_ref, place_type, name, geom, population, population_source, fetched_at)
               SELECT DISTINCT ON (source, source_ref) source, source_ref, place_type, name,
                      ST_SetSRID(ST_GeomFromWKB(wkb), 4326), population, population_source, now()
               FROM place_load
               ON CONFLICT (source, source_ref) DO UPDATE SET place_type = EXCLUDED.place_type, name = EXCLUDED.name,
                   geom = EXCLUDED.geom, population = EXCLUDED.population,
                   population_source = EXCLUDED.population_source, fetched_at = now(), updated_at = now()"""
        )
        return int(result.split()[-1])

async def upsert_admin_areas(conn: asyncpg.Connection, areas: list[AdminArea]) -> int:
    if not areas:
        return 0
    # Own transaction (a savepoint when nested): the ON COMMIT DROP temp table must live until the INSERT.
    async with conn.transaction():
        await conn.execute(
            """CREATE TEMP TABLE IF NOT EXISTS admin_load (source_ref text, admin_level smallint, name text,
                   name_norm text, wkb bytea) ON COMMIT DROP"""
        )
        await conn.copy_records_to_table("admin_load", records=[
            (a.source_ref, a.admin_level, a.name, a.name_norm, a.geom.wkb) for a in areas])
        result = await conn.execute(
            """INSERT INTO admin_areas (source_ref, admin_level, name, name_norm, geom, fetched_at)
               SELECT DISTINCT ON (source_ref) source_ref, admin_level, name, name_norm,
                      ST_Multi(ST_SetSRID(ST_GeomFromWKB(wkb), 4326)), now()
               FROM admin_load
               ON CONFLICT (source_ref) DO UPDATE SET admin_level = EXCLUDED.admin_level, name = EXCLUDED.name,
                   name_norm = EXCLUDED.name_norm, geom = EXCLUDED.geom, fetched_at = now()"""
        )
        return int(result.split()[-1])


async def upsert_supplier_stock(conn: asyncpg.Connection, rows: list) -> list:
    """External stock (e.g. AePDS): site facility (source datagov) -> supplier -> stock row.

    Returns SupplierStockUpdated events for rows whose quantity changed (publish after commit).
    """
    from reliefroute_contracts.events import SupplierStockUpdated

    events = []
    async with conn.transaction():
        for r in rows:
            facility_id = await conn.fetchval(
                """INSERT INTO facilities (source, source_ref, kind, name, geom, attributes, fetched_at)
                   VALUES ('datagov', $1, 'godown', $2, ST_SetSRID(ST_MakePoint($3, $4), 4326), $5, now())
                   ON CONFLICT (source, source_ref) DO UPDATE SET name = EXCLUDED.name, geom = EXCLUDED.geom,
                       attributes = EXCLUDED.attributes, fetched_at = now(), updated_at = now()
                   RETURNING id""",
                r.external_ref, r.site_name, r.lon, r.lat, r.attributes,
            )  # fmt: skip
            supplier_id = await conn.fetchval("SELECT id FROM suppliers WHERE facility_id = $1 ORDER BY created_at LIMIT 1",
                                              facility_id)
            if supplier_id is None:
                # Government system of record: identity is verified by definition.
                supplier_id = await conn.fetchval(
                    "INSERT INTO suppliers (facility_id, name, preferred_language, verified) VALUES ($1, $2, 'te', true) RETURNING id",
                    facility_id, r.supplier_name)
            previous = await conn.fetchval("SELECT quantity FROM stock WHERE supplier_id = $1 AND item_code = $2",
                                           supplier_id, r.item.value)
            await conn.execute(
                """INSERT INTO stock (supplier_id, item_code, quantity, unit_weight_kg, reported_at, reported_via)
                   VALUES ($1, $2, $3, $4, $5, 'api')
                   ON CONFLICT (supplier_id, item_code) DO UPDATE SET quantity = EXCLUDED.quantity,
                       unit_weight_kg = EXCLUDED.unit_weight_kg, reported_at = EXCLUDED.reported_at,
                       reported_via = 'api', updated_at = now()""",
                supplier_id, r.item.value, r.quantity, r.unit_weight_kg, r.reported_at,
            )  # fmt: skip
            if previous != r.quantity:
                events.append(SupplierStockUpdated(supplier_id=supplier_id, item_code=r.item, quantity=r.quantity,
                                                   previous_quantity=previous, reported_at=r.reported_at,
                                                   reported_via="api"))  # fmt: skip
    return events
