"""Geometry verifier and exclusion polygons. Shapes are built in the test from a local
origin plus metre offsets, so nothing here resembles runtime data."""

import pytest
from shapely import Point, Polygon

from app.solvers.geometry import HazardShape, exclusion_rings, local_projection, verify_route

ORIGIN = Point(80.0, 16.0)  # test frame origin only


def _frame():
    fwd, inv = local_projection([ORIGIN])
    ox, oy = fwd.transform(ORIGIN.x, ORIGIN.y)
    return lambda dx, dy: inv.transform(ox + dx, oy + dy)


def _square(at, x0, y0, size) -> Polygon:
    return Polygon([at(x0, y0), at(x0 + size, y0), at(x0 + size, y0 + size), at(x0, y0 + size)])


@pytest.fixture
def hazard():
    at = _frame()
    return at, HazardShape("h1", _square(at, 0, 0, 1000))


def test_route_through_hazard_fails_with_overlap(hazard):
    at, h = hazard
    verdict = verify_route([at(-500, 500), at(1500, 500)], [h])
    assert not verdict.passed
    [v] = verdict.violations
    assert v.kind == "intersects" and v.overlap_m == pytest.approx(1000, rel=0.01)
    assert "enters hazard h1 for 1000 m" in verdict.explain()[0]


def test_route_near_hazard_fails_clearance(hazard):
    at, h = hazard
    verdict = verify_route([at(-500, 1100), at(1500, 1100)], [h], min_clearance_m=200)
    assert not verdict.passed
    assert verdict.violations[0].kind == "too_close"
    assert verdict.violations[0].distance_m == pytest.approx(100, rel=0.02)


def test_route_clear_of_hazard_passes(hazard):
    at, h = hazard
    verdict = verify_route([at(-500, 1500), at(1500, 1500)], [h], min_clearance_m=200)
    assert verdict.passed and verdict.min_clearance_m == pytest.approx(500, rel=0.02)


def test_no_hazards_passes():
    at = _frame()
    assert verify_route([at(0, 0), at(10, 10)], []).passed


def test_exclusion_rings_cover_hazard_with_margin(hazard):
    at, h = hazard
    [ring] = exclusion_rings([h], margin_m=250, simplify_tolerance_m=100)
    excl = Polygon(ring)
    assert excl.contains(h.geom)
    assert ring[0] == ring[-1]
    # a point 150 m outside the hazard is inside the 250 m margin
    assert excl.contains(Point(at(1150, 500)))


def test_overlapping_hazards_merge(hazard):
    at, h = hazard
    other = HazardShape("h2", _square(at, 900, 0, 1000))
    far = HazardShape("h3", _square(at, 10_000, 0, 500))
    assert len(exclusion_rings([h, other, far])) == 2


def test_simplify_must_be_below_margin(hazard):
    with pytest.raises(ValueError):
        exclusion_rings([hazard[1]], margin_m=100, simplify_tolerance_m=100)


def test_vertex_budget_is_respected_and_still_covers():
    at = _frame()
    # A jagged 1000-vertex star around the origin.
    import math
    pts = [at(math.cos(t) * (800 + 200 * (k % 2)), math.sin(t) * (800 + 200 * (k % 2)))
           for k, t in enumerate(math.tau * i / 1000 for i in range(1000))]
    h = HazardShape("star", Polygon(pts))
    rings = exclusion_rings([h], margin_m=250, simplify_tolerance_m=10, max_vertices=40)
    assert sum(len(r) for r in rings) <= 40
    assert all(Polygon(r).contains(h.geom) for r in rings)
