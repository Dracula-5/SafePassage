"""Reads of B-owned data, through contract views only (v_active_hazards, v_places, v_admin_areas,
v_facilities, v_available_stock, v_suppliers, v_vehicle_status)."""

from __future__ import annotations

import asyncpg
from shapely import MultiPolygon, from_wkb

from app.routing.repository import ROAD_BLOCKING_SEVERITIES, ROAD_BLOCKING_TYPES

from .schemas import HazardFact, PopulationEstimate, ReliefPoint

Conn = asyncpg.Connection | asyncpg.Pool


async def hazard_facts(conn: Conn, hazard_ids: list[str]) -> list[HazardFact]:
    rows = await conn.fetch(
        """SELECT h.id::text AS hazard_id, h.source::text, h.hazard_type::text, h.severity::text, h.headline, h.area_desc,
                  ST_Area(h.geom::geography) / 1e6 AS area_km2, h.is_drill, h.expires_at,
                  COALESCE(f.is_stale, false) AS is_stale
           FROM v_active_hazards h LEFT JOIN v_source_freshness f ON f.source = h.source::text
           WHERE h.id = ANY($1::uuid[])""",
        hazard_ids,
    )
    return [HazardFact(**dict(r)) for r in rows]


async def hazard_union(conn: Conn, hazard_ids: list[str]) -> MultiPolygon | None:
    wkb = await conn.fetchval(
        "SELECT ST_AsBinary(ST_Multi(ST_Union(geom))) FROM v_active_hazards WHERE id = ANY($1::uuid[])", hazard_ids)
    return None if wkb is None else from_wkb(wkb)


async def population_in(conn: Conn, hazard_ids: list[str]) -> PopulationEstimate:
    """Census figures of settlements in the zone, without counting a whole city for a partial hit.

    A city or town has one figure at its centre point, so it is scaled by the share of its mandal
    (OSM admin_level 6) inside the zone, assuming even density across the mandal. Suburbs are
    skipped when their mandal's city is already counted: its figure includes them.
    """
    row = await conn.fetchrow(
        """WITH zone AS (SELECT ST_Union(geom) AS g FROM v_active_hazards WHERE id = ANY($1::uuid[])),
           inside AS (
               SELECT p.place_type, p.population, p.population_source, m.mandal_id, m.share
               FROM v_places p CROSS JOIN zone
               LEFT JOIN LATERAL (
                   SELECT a.id AS mandal_id,
                          ST_Area(ST_CollectionExtract(ST_Intersection(ST_MakeValid(a.geom), zone.g), 3)::geography)
                              / NULLIF(ST_Area(a.geom::geography), 0) AS share
                   FROM v_admin_areas a
                   WHERE a.admin_level = 6 AND ST_Contains(a.geom, p.geom)
                   ORDER BY ST_Area(a.geom) LIMIT 1
               ) m ON p.place_type IN ('city', 'town', 'suburb', 'neighbourhood')
               WHERE ST_Intersects(p.geom, zone.g)
           ),
           city_mandals AS (
               SELECT DISTINCT mandal_id FROM inside
               WHERE place_type IN ('city', 'town') AND population IS NOT NULL AND mandal_id IS NOT NULL
           )
           SELECT count(*) AS total, count(population) AS with_pop, COALESCE(sum(population), 0) AS unscaled,
                  round(COALESCE(sum(CASE
                      WHEN place_type IN ('city', 'town') THEN population * COALESCE(share, 1)
                      WHEN place_type IN ('suburb', 'neighbourhood')
                           AND mandal_id IN (SELECT mandal_id FROM city_mandals) THEN 0
                      ELSE population END), 0))::bigint AS pop,
                  count(*) FILTER (WHERE place_type IN ('city', 'town') AND population IS NOT NULL
                                     AND share < 1) AS scaled,
                  array_remove(array_agg(DISTINCT population_source), NULL) AS sources
           FROM inside""",
        hazard_ids,
    )
    return PopulationEstimate(population_lower_bound=row["pop"], population_unscaled=row["unscaled"],
                              places_with_population=row["with_pop"], places_total=row["total"],
                              places_scaled=row["scaled"], sources=sorted(row["sources"] or []))  # fmt: skip


async def relief_points(conn: Conn, hazard_ids: list[str], radius_m: float, limit: int,
                        min_distance_m: float = 0) -> list[ReliefPoint]:
    """Shelters/schools/hospitals OUTSIDE the hazard but within radius_m of it, nearest first.

    Relief goes to safe sites next to the affected area. Sites inside the hazard, or inside any
    other hazard that closes roads, are excluded, and so are sites closer than min_distance_m: the
    router keeps away from hazards by a margin, so it could never reach them.
    """
    rows = await conn.fetch(
        """WITH zone AS (SELECT ST_Union(geom) AS g FROM v_active_hazards
                         WHERE is_drill OR id = ANY($1::uuid[])
                            OR (hazard_type::text = ANY($4) AND severity::text = ANY($5)))
           , target AS (SELECT ST_Union(geom) AS g FROM v_active_hazards WHERE id = ANY($1::uuid[]))
           SELECT f.id::text AS facility_id, f.name, f.kind::text AS kind, ST_X(f.geom) AS lon, ST_Y(f.geom) AS lat,
                  ST_Distance(f.geom::geography, target.g::geography) AS distance_to_hazard_m
           FROM v_facilities f, zone, target
           WHERE f.kind IN ('shelter', 'school', 'hospital')
             AND ST_DWithin(f.geom::geography, target.g::geography, $2)
             AND NOT ST_DWithin(f.geom::geography, zone.g::geography, $6)
           ORDER BY (f.kind = 'shelter') DESC, distance_to_hazard_m
           LIMIT $3""",
        hazard_ids, radius_m, limit, ROAD_BLOCKING_TYPES, ROAD_BLOCKING_SEVERITIES, min_distance_m,
    )  # fmt: skip
    return [ReliefPoint(**dict(r), share=0) for r in rows]


async def available_stock(conn: Conn, item_codes: list[str]) -> list[asyncpg.Record]:
    return await conn.fetch(
        """SELECT s.stock_id::text, s.supplier_id::text, s.item_code::text, s.available, s.unit_weight_kg, s.reported_at,
                  sp.name AS supplier_name, sp.verified, sp.facility_id::text, ST_X(sp.geom) AS lon, ST_Y(sp.geom) AS lat
           FROM v_available_stock s JOIN v_suppliers sp ON sp.id = s.supplier_id
           WHERE s.item_code::text = ANY($1) AND s.available > 0""",
        item_codes,
    )


async def usable_vehicles(conn: Conn) -> list[asyncpg.Record]:
    """Online vehicles with a known position that are not already on an active assignment."""
    return await conn.fetch(
        """SELECT v.id::text, v.label, v.capacity_kg, v.driver_language, v.driver_phone,
                  ST_X(v.last_position) AS lon, ST_Y(v.last_position) AS lat, v.last_seen_at
           FROM v_vehicle_status v
           WHERE NOT v.is_offline AND v.last_position IS NOT NULL AND v.status <> 'maintenance'
             AND NOT EXISTS (SELECT 1 FROM v_vehicle_assignment a WHERE a.vehicle_id = v.id)"""
    )


async def offline_vehicle_count(conn: Conn) -> int:
    return await conn.fetchval("SELECT count(*) FROM v_vehicle_status WHERE is_offline")
