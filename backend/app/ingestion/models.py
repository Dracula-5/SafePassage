"""Normalized records every source produces before they touch the database."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
from reliefroute_contracts.enums import FacilityKind, FacilitySource, HazardSeverity, HazardSource, HazardStatus, HazardType
from shapely import MultiPolygon, Point, Polygon, make_valid
from shapely.geometry.base import BaseGeometry


def to_multipolygon(geom: BaseGeometry) -> MultiPolygon:
    """Repair and keep only the polygonal parts; raises ValueError if nothing polygonal is left."""
    fixed = make_valid(geom)
    parts: list[Polygon] = []
    stack = [fixed]
    while stack:
        g = stack.pop()
        if isinstance(g, Polygon) and not g.is_empty and g.area > 0:
            parts.append(g)
        elif hasattr(g, "geoms"):
            stack.extend(g.geoms)
    if not parts:
        raise ValueError("geometry has no polygonal area")
    return MultiPolygon(parts)


class NormalizedHazard(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    source: HazardSource
    source_ref: str = Field(min_length=1)
    dedupe_key: str = Field(min_length=1)
    hazard_type: HazardType
    severity: HazardSeverity
    status: HazardStatus = HazardStatus.ACTIVE
    headline: str = Field(min_length=1)
    description: str | None = None
    area_desc: str | None = None
    geom: MultiPolygon
    effective_at: datetime | None = None
    expires_at: datetime | None = None
    issued_at: datetime | None = None
    raw: dict = Field(default_factory=dict)

    @field_validator("geom", mode="before")
    @classmethod
    def _polygonal(cls, value: BaseGeometry) -> MultiPolygon:
        return to_multipolygon(value)

    def content_hash(self) -> str:
        """Hash of everything that matters to consumers; unchanged hash = no new version."""
        material = json.dumps(
            [self.hazard_type, self.severity, self.status, self.headline, self.description, self.area_desc,
             self.geom.wkb_hex, str(self.effective_at), str(self.expires_at)],
            sort_keys=True,
        )
        return hashlib.sha256(material.encode()).hexdigest()


class NormalizedFacility(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    source: FacilitySource
    source_ref: str
    kind: FacilityKind
    name: str = Field(min_length=1)
    geom: Point
    capacity_people: int | None = Field(default=None, ge=0)
    attributes: dict = Field(default_factory=dict)
