"""Gate 1 evidence: a real route that avoids a real hazard polygon.

Live inputs only:
- hazard: the newest polygon from the NDMA SACHET public feed around which Valhalla can route
  (alerts outside the graph's coverage fail fast with "no road near location" and are skipped);
- roads: self-hosted Valhalla built from the OSM Geofabrik extract.
Origin/destination are derived from the hazard polygon itself: points beyond its edges on
opposite sides, so the unconstrained shortest path tends to cross it.

Needs VALHALLA_URL (e.g. http://localhost:8002) and internet access to sachet.ndma.gov.in.
Run with `-s` to see the printed evidence.
"""

import os

import httpx
import pytest

from app.core.resilience import CircuitBreaker
from app.routing.valhalla import Location, LocationNotRoutableError, NoRouteError, ValhallaClient
from app.solvers.geometry import HazardShape, exclusion_rings, verify_route

from .live_sources import endpoints_around, live_hazards

pytestmark = pytest.mark.integration
VALHALLA_URL = os.environ.get("VALHALLA_URL")
async def test_real_route_avoids_real_hazard():
    if not VALHALLA_URL:
        pytest.skip("VALHALLA_URL not set")
    async with httpx.AsyncClient() as http:
        valhalla = ValhallaClient(VALHALLA_URL, http, breaker=CircuitBreaker("valhalla"))
        tried = 0
        async for guid, sender, title, poly in live_hazards(http):
            hazard = HazardShape(hazard_id=f"sachet:{guid}", geom=poly)
            for origin, dest in endpoints_around(poly):
                tried += 1
                try:
                    direct = await valhalla.route([origin, dest])
                except (NoRouteError, LocationNotRoutableError):
                    continue
                before = verify_route(direct.shape, [hazard])
                if before.passed:
                    continue  # this pair does not cross the hazard; try another
                rings = exclusion_rings([hazard], margin_m=250, simplify_tolerance_m=100)
                safe = await valhalla.route([origin, dest], exclude=rings)
                after = verify_route(safe.shape, [hazard], min_clearance_m=0)
                print(f"\nSACHET alert {guid} from {sender}: {title[:90]}")
                print(f"hazard polygon: {len(poly.exterior.coords)} vertices, bounds {tuple(round(v, 4) for v in poly.bounds)}")
                print(f"origin {origin.lat:.5f},{origin.lon:.5f} -> destination {dest.lat:.5f},{dest.lon:.5f}")
                print(f"UNCONSTRAINED: {direct.distance_m / 1000:.1f} km, {direct.duration_s / 60:.0f} min, "
                      f"verdict={'PASS' if before.passed else 'FAIL'} {before.explain()}")
                print(f"HAZARD-AWARE : {safe.distance_m / 1000:.1f} km, {safe.duration_s / 60:.0f} min, "
                      f"verdict={'PASS' if after.passed else 'FAIL'}, min clearance {after.min_clearance_m:.0f} m, "
                      f"exclusion ring vertices {[len(r) for r in rings]}")
                assert after.passed, after.explain()
                assert safe.distance_m >= direct.distance_m
                return
        pytest.fail(f"no live SACHET polygon inside Valhalla coverage produced a crossing route ({tried} pairs tried)")


async def test_matrix_respects_exclusions():
    """A matrix call with the same exclusions must agree with the route call on reachability."""
    if not VALHALLA_URL:
        pytest.skip("VALHALLA_URL not set")
    async with httpx.AsyncClient() as http:
        valhalla = ValhallaClient(VALHALLA_URL, http, breaker=CircuitBreaker("valhalla"))
        async for _, _, _, poly in live_hazards(http):
            rings = exclusion_rings([HazardShape("h", poly)])
            for origin, dest in endpoints_around(poly):
                try:
                    route = await valhalla.route([origin, dest], exclude=rings)
                except (NoRouteError, LocationNotRoutableError):
                    continue
                m = await valhalla.matrix([origin], [origin, dest], exclude=rings)
                print(f"\nmatrix time {m.durations_s[0][1]:.0f} s vs route time {route.duration_s:.0f} s")
                assert m.durations_s[0][0] == 0
                assert m.durations_s[0][1] == pytest.approx(route.duration_s, rel=0.15)
                return
        pytest.fail("no routable live hazard found")
