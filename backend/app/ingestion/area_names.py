"""Resolve CAP <areaDesc> place names to official OSM administrative boundaries.

Fallback for SACHET alerts whose polygon file cannot be downloaded. Only unambiguous name
matches are used: a name that matches several areas (and cannot be narrowed to the issuing
state) is reported as ambiguous and skipped, never guessed.

Examples of areaDesc seen live:
  "kkd-sankhavaram, kkd-gollaprolu, kkd-kothapalle, pln-gurazala mandals "   (AP SDMA, mandals)
  "Srikakulam, Vizianagaram & Visakhapatnam"                                (IMD, districts)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import asyncpg
from shapely import MultiPolygon, from_wkb
from shapely.ops import unary_union

SUFFIXES = ("mandalam", "mandal", "taluka", "taluk", "tehsil", "district", "division")
MANDAL_LEVEL, DISTRICT_LEVEL, STATE_LEVEL = 6, 5, 4


def normalize(name: str) -> str:
    """'Gollaprolu Mandal' / 'gollaprolu mandals' / 'GOLLAPROLU' -> 'gollaprolu'."""
    words = re.sub(r"[^a-z ]", " ", name.lower()).split()
    return " ".join(w for w in words if w not in SUFFIXES and w.removesuffix("s") not in SUFFIXES)


def split_area_desc(area_desc: str) -> tuple[list[str], int | None]:
    """Return (normalized names, admin level hinted by the text or None)."""
    lowered = area_desc.lower()
    level = MANDAL_LEVEL if "mandal" in lowered else DISTRICT_LEVEL if "district" in lowered else None
    names = []
    for part in re.split(r",|&|\band\b|;", area_desc):
        part = re.sub(r"^\s*[a-z]{2,5}-", "", part.strip().lower())  # AP SDMA district code prefix, e.g. "kkd-"
        norm = normalize(part)
        if norm:
            names.append(norm)
    return names, level


@dataclass
class AreaMatch:
    geom: MultiPolygon | None
    matched: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)


# Candidates must lie in the issuing state when the sender names one (e.g. "Andhra-Pradesh-SDMA").
MATCH_SQL = """
WITH state AS (
    SELECT geom FROM admin_areas
    WHERE admin_level = 4 AND $3 ILIKE '%' || replace(name, ' ', '%') || '%'
    LIMIT 1
)
SELECT a.source_ref, ST_AsBinary(a.geom) AS wkb
FROM admin_areas a
WHERE a.name_norm = $1 AND a.admin_level = ANY($2::smallint[])
  AND (NOT EXISTS (SELECT 1 FROM state) OR ST_Within(ST_PointOnSurface(a.geom), (SELECT geom FROM state)))
"""


async def resolve(conn: asyncpg.Connection | asyncpg.Pool, area_desc: str, sender: str = "") -> AreaMatch:
    names, level = split_area_desc(area_desc)
    levels = [level] if level else [DISTRICT_LEVEL, MANDAL_LEVEL]
    result = AreaMatch(geom=None)
    geoms = []
    for name in names:
        rows = await conn.fetch(MATCH_SQL, name, levels, sender.replace("-", " "))
        if len(rows) == 1:
            result.matched.append(name)
            geoms.append(from_wkb(rows[0]["wkb"]))
        elif rows:
            result.ambiguous.append(name)
        else:
            result.unmatched.append(name)
    if geoms:
        merged = unary_union(geoms)
        result.geom = merged if isinstance(merged, MultiPolygon) else MultiPolygon([merged])
    return result
