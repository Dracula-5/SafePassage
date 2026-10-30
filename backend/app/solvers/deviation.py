"""Where a vehicle is relative to its planned route. Distances are metres in a local UTM
projection, like the rest of the geometry code; lon/lat degrees are never used as distances."""

from __future__ import annotations

from collections.abc import Sequence

from shapely import LineString, Point
from shapely.ops import substring

from .geometry import LonLat, _project, local_projection


def distance_m(a: LonLat, b: LonLat) -> float:
    fwd, _ = local_projection([Point(a), Point(b)])
    return _project(Point(a), fwd).distance(_project(Point(b), fwd))


def distance_from_route_m(route: Sequence[LonLat], point: LonLat) -> float:
    line = LineString(route)
    fwd, _ = local_projection([line])
    return _project(line, fwd).distance(_project(Point(point), fwd))


def remaining_route(route: Sequence[LonLat], point: LonLat) -> list[LonLat]:
    """The route still ahead of `point`, from the nearest spot on the line to the end.

    A route that doubles back can match its later pass first; callers use this for hazard checks,
    where seeing less of the route is the cost of not flagging roads already driven.
    """
    line = LineString(route)
    fwd, inv = local_projection([line])
    line_m = _project(line, fwd)
    ahead = substring(line_m, line_m.project(_project(Point(point), fwd)), line_m.length)
    if not isinstance(ahead, LineString) or ahead.length < 1:
        return [tuple(c) for c in route[-2:]]
    return [(x, y) for x, y in _project(ahead, inv).coords]
