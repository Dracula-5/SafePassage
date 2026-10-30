"""Reads B-owned data through the contract views only (v_active_hazards, v_vehicle_status)."""

from __future__ import annotations

from datetime import datetime

import asyncpg
from pydantic import BaseModel
from shapely import MultiPolygon, Polygon, from_geojson

from app.solvers.geometry import HazardShape

from .valhalla import Location


class VehicleSnapshot(BaseModel):
    vehicle_id: str
    label: str
    capacity_kg: float
    status: str
    position: Location | None
    last_seen_at: datetime | None
    is_offline: bool


async def fetch_active_hazards(
    conn: asyncpg.Connection | asyncpg.Pool,
    bbox: tuple[float, float, float, float] | None = None,
    include_drill: bool = True,
) -> list[HazardShape]:
    """Active hazards, optionally limited to those intersecting bbox (min_lon, min_lat, max_lon, max_lat)."""
    rows = await conn.fetch(
        """
        SELECT id::text, severity::text, ST_AsGeoJSON(geom) AS geojson
        FROM v_active_hazards
        WHERE ($1::float8 IS NULL OR geom && ST_MakeEnvelope($1, $2::float8, $3::float8, $4::float8, 4326))
          AND ($5 OR NOT is_drill)
        """,
        *(bbox or (None, None, None, None)),
        include_drill,
    )
    return [_shape(row) for row in rows]


def _shape(row: asyncpg.Record) -> HazardShape:
    geom = from_geojson(row["geojson"])
    if not isinstance(geom, Polygon | MultiPolygon):
        raise TypeError(f"hazard {row['id']} geometry is {geom.geom_type}, expected (Multi)Polygon")
    return HazardShape(hazard_id=row["id"], geom=geom, severity=row["severity"])


# Most active alerts do not close a road. A rainfall forecast for next week or a lightning warning
# covering 3,000 km2 is a risk to report; treating it as a closure leaves nothing routable.
ROAD_BLOCKING_TYPES = ["flood", "cyclone", "landslide", "tsunami", "earthquake", "wildfire"]
ROAD_BLOCKING_SEVERITIES = ["severe", "extreme"]


def blocks_roads(hazard_type: str, severity: str, is_drill: bool) -> bool:
    return is_drill or (hazard_type in ROAD_BLOCKING_TYPES and severity in ROAD_BLOCKING_SEVERITIES)


async def fetch_road_hazards(
    conn: asyncpg.Connection | asyncpg.Pool,
    bbox: tuple[float, float, float, float],
    plan_hazard_ids: list[str],
) -> list[HazardShape]:
    """Hazards a route must stay out of: those that close roads, and those this plan responds to."""
    rows = await conn.fetch(
        """
        SELECT id::text, severity::text, ST_AsGeoJSON(geom) AS geojson
        FROM v_active_hazards
        WHERE geom && ST_MakeEnvelope($1, $2, $3, $4, 4326)
          AND (is_drill OR id = ANY($5::uuid[])
               OR (hazard_type::text = ANY($6) AND severity::text = ANY($7)))
        """,
        *bbox, plan_hazard_ids, ROAD_BLOCKING_TYPES, ROAD_BLOCKING_SEVERITIES,
    )  # fmt: skip
    return [_shape(row) for row in rows]


async def fetch_vehicles(conn: asyncpg.Connection | asyncpg.Pool) -> list[VehicleSnapshot]:
    rows = await conn.fetch(
        """
        SELECT id::text, label, capacity_kg, status::text, last_seen_at, is_offline,
               ST_X(last_position) AS lon, ST_Y(last_position) AS lat
        FROM v_vehicle_status
        ORDER BY label
        """
    )
    return [
        VehicleSnapshot(
            vehicle_id=r["id"], label=r["label"], capacity_kg=r["capacity_kg"], status=r["status"],
            position=None if r["lon"] is None else Location(lon=r["lon"], lat=r["lat"]),
            last_seen_at=r["last_seen_at"], is_offline=r["is_offline"],
        )  # fmt: skip
        for r in rows
    ]
