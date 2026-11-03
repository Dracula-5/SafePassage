"""B endpoints: hazards, facilities, vehicles, drill, source health.

Reads B's own tables directly (B owns them) and staleness via v_source_freshness.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from redis.exceptions import RedisError
from reliefroute_contracts.enums import FacilityKind, HazardSeverity, HazardType
from shapely import Polygon
from shapely.errors import GEOSException
from shapely.geometry import shape
from shapely.validation import explain_validity

from app.core import bus
from app.core.auth import require_operator
from app.core.db import get_pool
from app.core.logging import get_logger
from app.core.validation import Name

from ..accounts.router import router as accounts_router
from ..suppliers.router import router as portal_router
from ..telemetry.demo import router as demo_router
from ..telemetry.requests import router as requests_router
from ..telemetry.router import VEHICLE_SELECT, vehicle_out
from ..telemetry.router import router as telemetry_router
from .stream import router as stream_router
from ..ingestion.store import publish_hazard_events, set_hazard_status, upsert_hazard
from ..ingestion.models import NormalizedHazard
from ..ingestion.settings import get_senses_settings

log = get_logger(__name__)
router = APIRouter(tags=["senses"])
# Sub-routers live in their own packages; main.py only discovers this module's `router`.
router.include_router(telemetry_router)
router.include_router(demo_router)
router.include_router(requests_router)
router.include_router(portal_router)
router.include_router(stream_router)
router.include_router(accounts_router)
GEOJSON = "application/geo+json"
MAX_DRILL_AREA_KM2 = 50_000  # larger than any district; stops accidental continent-sized drills
MAX_DRILL_VERTICES = 1_000
MAX_FACILITIES = 5_000       # the map asks by viewport; an unbounded call would return every row
MAX_HAZARDS = 2_000
WORKERS = ("brain", "ingestion")


def _bbox(value: str | None) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in value.split(","))
    except ValueError as exc:
        raise HTTPException(422, "bbox must be min_lon,min_lat,max_lon,max_lat") from exc
    if not (-180 <= x0 < x1 <= 180 and -90 <= y0 < y1 <= 90):
        raise HTTPException(422, "bbox out of range or inverted")
    return x0, y0, x1, y1


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


HAZARD_SELECT = """
SELECT h.id, h.source::text, h.source_ref, h.hazard_type::text, h.severity::text, h.status::text, h.headline,
       h.description, h.area_desc, h.effective_at, h.expires_at, h.issued_at, h.fetched_at, h.version,
       ST_AsGeoJSON(h.geom, 6) AS geojson, COALESCE(f.is_stale, false) AS is_stale
FROM hazards h LEFT JOIN v_source_freshness f ON f.source = h.source::text
"""


def _hazard_feature(r) -> dict:
    return {
        "type": "Feature", "id": str(r["id"]), "geometry": json.loads(r["geojson"]),
        "properties": {
            "id": str(r["id"]), "source": r["source"], "source_ref": r["source_ref"], "hazard_type": r["hazard_type"],
            "severity": r["severity"], "status": r["status"], "headline": r["headline"], "description": r["description"],
            "area_desc": r["area_desc"], "effective_at": _iso(r["effective_at"]), "expires_at": _iso(r["expires_at"]),
            "issued_at": _iso(r["issued_at"]), "fetched_at": _iso(r["fetched_at"]), "version": r["version"],
            "is_drill": r["source"] == "drill", "is_stale": r["is_stale"],
        },
    }  # fmt: skip


@router.get("/api/status")
async def public_status() -> dict:
    """Small enough to ask for every 5 seconds. `hazards_changed` says when the map's data needs reloading."""
    pool = await get_pool()
    hazards = await pool.fetchrow(
        """SELECT count(*) AS active, count(*) FILTER (WHERE severity IN ('severe', 'extreme')) AS severe,
                  max(updated_at) AS changed FROM v_active_hazards""")
    sources = await pool.fetchrow("SELECT count(*) AS total, count(*) FILTER (WHERE NOT is_stale) AS fresh FROM v_source_freshness")
    return {"hazards_active": hazards["active"], "hazards_severe": hazards["severe"], "sources_fresh": sources["fresh"],
            "sources_total": sources["total"], "hazards_changed": f"{hazards['active']}:{_iso(hazards['changed'])}"}  # fmt: skip


@router.get("/api/hazards")
async def list_hazards(
    active: bool = True, include_drill: bool = True, bbox: Annotated[str | None, Query()] = None
) -> JSONResponse:
    box = _bbox(bbox)
    rows = await (await get_pool()).fetch(
        HAZARD_SELECT + """
        WHERE (NOT $1 OR (h.status = 'active' AND (h.expires_at IS NULL OR h.expires_at > now())))
          AND ($2 OR h.source <> 'drill')
          AND ($3::float8 IS NULL OR h.geom && ST_MakeEnvelope($3, $4::float8, $5::float8, $6::float8, 4326))
        ORDER BY array_position(ARRAY['extreme','severe','moderate','minor','unknown'], h.severity::text), h.fetched_at DESC
        LIMIT $7""",
        active, include_drill, *(box or (None, None, None, None)), MAX_HAZARDS,
    )  # fmt: skip
    return JSONResponse({"type": "FeatureCollection", "features": [_hazard_feature(r) for r in rows]}, media_type=GEOJSON)


@router.get("/api/facilities")
async def list_facilities(
    kind: Annotated[str | None, Query(description="comma-separated FacilityKind values")] = None,
    bbox: Annotated[str | None, Query()] = None,
) -> JSONResponse:
    kinds = None
    if kind:
        try:
            kinds = [FacilityKind(k).value for k in kind.split(",")]
        except ValueError as exc:
            raise HTTPException(422, f"unknown facility kind in {kind!r}") from exc
    box = _bbox(bbox)
    rows = await (await get_pool()).fetch(
        """SELECT f.id, f.source::text, f.source_ref, f.kind::text, f.name, f.capacity_people,
                  ST_AsGeoJSON(f.geom, 6) AS geojson,
                  EXISTS (SELECT 1 FROM suppliers s WHERE s.facility_id = f.id) AS is_supplier
           FROM facilities f
           WHERE ($1::text[] IS NULL OR f.kind::text = ANY($1))
             AND ($2::float8 IS NULL OR f.geom && ST_MakeEnvelope($2, $3::float8, $4::float8, $5::float8, 4326))
             AND f.name NOT LIKE 'TEST-SIM%'  -- a made-up presentation site is never shown as a place
           LIMIT $6""",
        kinds, *(box or (None, None, None, None)), MAX_FACILITIES,
    )  # fmt: skip
    features = [{
        "type": "Feature", "id": str(r["id"]),
        "geometry": json.loads(r["geojson"]),
        "properties": {"id": str(r["id"]), "source": r["source"], "source_ref": r["source_ref"], "kind": r["kind"],
                       "name": r["name"], "capacity_people": r["capacity_people"], "is_supplier": r["is_supplier"]},
    } for r in rows]  # fmt: skip
    return JSONResponse({"type": "FeatureCollection", "features": features}, media_type=GEOJSON)


@router.get("/api/vehicles", dependencies=[Depends(require_operator)])  # names and live truck positions
async def list_vehicles() -> list[dict]:
    return [vehicle_out(r) for r in await (await get_pool()).fetch(VEHICLE_SELECT + " ORDER BY label")]


class DrillRequest(BaseModel):
    geometry: dict
    hazard_type: HazardType
    severity: HazardSeverity
    label: Name = Field(min_length=1, max_length=200)
    ttl_minutes: int = Field(default=120, ge=5, le=1440)

    @field_validator("geometry")
    @classmethod
    def _polygon(cls, value: dict) -> dict:
        if value.get("type") != "Polygon":
            raise ValueError("geometry must be a GeoJSON Polygon")
        try:
            poly = shape(value)
        except (AttributeError, KeyError, TypeError, ValueError, IndexError, GEOSException) as exc:
            raise ValueError(f"invalid polygon: {exc}") from exc
        if not isinstance(poly, Polygon) or not poly.is_valid:
            raise ValueError(f"invalid polygon: {explain_validity(poly)}")
        if poly.area == 0:
            raise ValueError("polygon has no area")
        if sum(len(ring.coords) for ring in (poly.exterior, *poly.interiors)) > MAX_DRILL_VERTICES:
            raise ValueError(f"polygon has more than {MAX_DRILL_VERTICES} vertices")
        x0, y0, x1, y1 = poly.bounds
        if not (-180 <= x0 and x1 <= 180 and -90 <= y0 and y1 <= 90):
            raise ValueError("coordinates out of range")
        return value


def _in_region(geometry: dict) -> bool:
    """Roads are only routable inside the region; a drill elsewhere could never produce a plan."""
    x0, y0, x1, y1 = shape(geometry).bounds
    rx0, ry0, rx1, ry1 = get_senses_settings().region_bbox
    return x0 < rx1 and x1 > rx0 and y0 < ry1 and y1 > ry0


@router.post("/api/drill", status_code=201, dependencies=[Depends(require_operator)])
async def create_drill(body: DrillRequest) -> JSONResponse:
    if not _in_region(body.geometry):
        raise HTTPException(422, f"drill is outside the mapped region ({get_senses_settings().region_name})")
    ref = f"drill:{uuid4()}"
    now = datetime.now(UTC)
    hazard = NormalizedHazard(
        source="drill", source_ref=ref, dedupe_key=ref, hazard_type=body.hazard_type, severity=body.severity,
        headline=f"DRILL: {body.label}", description="What-if hazard drawn by an operator. Not a real alert.",
        geom=shape(body.geometry), effective_at=now, expires_at=now + timedelta(minutes=body.ttl_minutes),
        issued_at=now, raw={"drawn_geometry": body.geometry},
    )  # fmt: skip
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        area = await conn.fetchval("SELECT ST_Area(ST_GeomFromGeoJSON($1)::geography) / 1e6", json.dumps(body.geometry))
        if area > MAX_DRILL_AREA_KM2:
            raise HTTPException(422, f"drill area {area:.0f} km2 exceeds {MAX_DRILL_AREA_KM2} km2")
        event = await upsert_hazard(conn, hazard)
        row = await conn.fetchrow(HAZARD_SELECT + " WHERE h.id = $1", event.hazard_id)
    try:
        await publish_hazard_events([event])
    except (RedisError, OSError) as exc:  # the drill is on the map; a plan can still be triggered by hand
        log.warning("drill_event_not_published", hazard_id=str(event.hazard_id), error=str(exc))
    return JSONResponse(_hazard_feature(row), status_code=201, media_type=GEOJSON)


@router.delete("/api/drill/{hazard_id}", status_code=204, dependencies=[Depends(require_operator)])
async def cancel_drill(hazard_id: UUID) -> Response:
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        source = await conn.fetchval("SELECT source::text FROM hazards WHERE id = $1", hazard_id)
        if source != "drill":
            raise HTTPException(404, "no drill hazard with that id")
        event = await set_hazard_status(conn, hazard_id, "cancelled")
    if event:
        try:
            await publish_hazard_events([event])
        except (RedisError, OSError) as exc:  # the drill is ended; the map catches up on its next reload
            log.warning("drill_event_not_published", hazard_id=str(event.hazard_id), error=str(exc))
    return Response(status_code=204)


class SourceHealthOut(BaseModel):
    source: str
    breaker_state: Literal["closed", "open", "half_open"]
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_error: str | None
    ttl_seconds: int
    fallback_source: str | None
    is_stale: bool


@router.get("/api/sources/health")
async def list_source_health() -> list[SourceHealthOut]:
    rows = await (await get_pool()).fetch(
        """SELECT source, breaker_state, last_success_at, last_failure_at, last_error, ttl_seconds,
                  fallback_source, is_stale FROM v_source_freshness ORDER BY source""")
    return [SourceHealthOut(**dict(r)) for r in rows] + await _worker_health()


async def _worker_health() -> list[SourceHealthOut]:
    """A dead worker raises no error anywhere: plans just stop. So it gets a line of its own."""
    out = []
    for worker in WORKERS:
        try:
            beat = await bus.last_heartbeat(worker)
        except (RedisError, OSError):
            beat = None
        out.append(SourceHealthOut(
            source=f"{worker}_worker", breaker_state="closed", last_success_at=beat, last_failure_at=None,
            last_error=None if beat else "no heartbeat: this worker is not running", ttl_seconds=bus.HEARTBEAT_TTL_S,
            fallback_source=None, is_stale=beat is None))  # fmt: skip
    return out
