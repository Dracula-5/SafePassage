"""Hazard geometry: exclusion polygons for the router and verification of planned routes.

All distances are computed in metres in a local UTM projection chosen from the data's
centroid; lon/lat degrees are never used as distances.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pyproj import CRS, Transformer
import shapely
from shapely import Geometry, LineString, MultiPolygon, Polygon, make_valid
from shapely.ops import unary_union

LonLat = tuple[float, float]


@dataclass(frozen=True)
class HazardShape:
    """A hazard as the solvers see it. `geom` is lon/lat (EPSG:4326)."""

    hazard_id: str
    geom: Polygon | MultiPolygon
    severity: str = "unknown"


class Violation(BaseModel):
    model_config = ConfigDict(frozen=True)

    hazard_id: str
    kind: Literal["intersects", "too_close"]
    distance_m: float = Field(ge=0, description="0 when intersecting")
    overlap_m: float = Field(ge=0, description="Length of route inside the hazard")


class RouteVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: bool
    min_clearance_m: float | None = Field(description="Distance to nearest hazard; None if no hazards")
    violations: list[Violation]

    def explain(self) -> list[str]:
        """Human-readable violations for the Critic's rejection message."""
        return [
            f"route enters hazard {v.hazard_id} for {v.overlap_m:.0f} m"
            if v.kind == "intersects"
            else f"route passes {v.distance_m:.0f} m from hazard {v.hazard_id}"
            for v in self.violations
        ]


@lru_cache(maxsize=64)
def _utm_transformers(zone: int, south: bool) -> tuple[Transformer, Transformer]:
    crs = CRS.from_dict({"proj": "utm", "zone": zone, "south": south})
    fwd = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    inv = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    return fwd, inv


def local_projection(geoms: Sequence[Geometry]) -> tuple[Transformer, Transformer]:
    """Forward/inverse transformers for the UTM zone containing the data's centroid."""
    centroid = unary_union(list(geoms)).centroid
    zone = int((centroid.x + 180) // 6) + 1
    return _utm_transformers(zone, centroid.y < 0)


def _project(geom: Geometry, transformer: Transformer) -> Geometry:
    return shapely.transform(geom, transformer.transform, interleaved=False)


def _polygons(geom: Geometry) -> list[Polygon]:
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for part in getattr(geom, "geoms", []) for g in _polygons(part)]


def exclusion_rings(
    hazards: Sequence[HazardShape], margin_m: float = 250.0, simplify_tolerance_m: float = 100.0
) -> list[list[list[float]]]:
    """Polygons to pass to Valhalla `exclude_polygons`, as rings of [lon, lat].

    Each hazard is grown by `margin_m` and then simplified with a tolerance smaller than the
    margin, so the result always still contains the original hazard while staying small
    enough for the router. Overlapping hazards are merged.
    """
    if not hazards:
        return []
    if simplify_tolerance_m >= margin_m:
        raise ValueError("simplify tolerance must be smaller than the margin to keep hazards covered")
    fwd, inv = local_projection([h.geom for h in hazards])
    merged = unary_union([make_valid(_project(h.geom, fwd)).buffer(margin_m) for h in hazards])
    rings = []
    for poly in _polygons(merged):
        simple = Polygon(poly.exterior).simplify(simplify_tolerance_m, preserve_topology=True)
        lonlat = _project(simple, inv)
        rings.append([[round(x, 6), round(y, 6)] for x, y in lonlat.exterior.coords])
    return rings


def verify_route(
    route: Sequence[LonLat], hazards: Sequence[HazardShape], min_clearance_m: float = 0.0
) -> RouteVerdict:
    """Check a route polyline against hazard polygons.

    Fails if the route intersects any hazard, or passes closer than `min_clearance_m`.
    """
    if len(route) < 2:
        raise ValueError("a route needs at least two points")
    if not hazards:
        return RouteVerdict(passed=True, min_clearance_m=None, violations=[])
    line = LineString(route)
    fwd, _ = local_projection([line, *(h.geom for h in hazards)])
    line_m = _project(line, fwd)
    violations: list[Violation] = []
    nearest = float("inf")
    for hazard in hazards:
        geom_m = make_valid(_project(hazard.geom, fwd))
        distance = line_m.distance(geom_m)
        nearest = min(nearest, distance)
        if line_m.intersects(geom_m):
            overlap = line_m.intersection(geom_m).length
            violations.append(Violation(hazard_id=hazard.hazard_id, kind="intersects", distance_m=0, overlap_m=overlap))
        elif distance < min_clearance_m:
            violations.append(Violation(hazard_id=hazard.hazard_id, kind="too_close", distance_m=distance, overlap_m=0))
    return RouteVerdict(passed=not violations, min_clearance_m=nearest, violations=violations)
