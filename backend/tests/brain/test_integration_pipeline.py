"""Gate 1 pipeline on live data: SACHET hazard -> PostGIS -> contract view -> exclusions ->
Valhalla matrix -> OR-Tools VRP -> Valhalla routes -> Shapely verification.

The hazard row is written into a throwaway test database (in production B writes it); the
code under test only reads it back through v_active_hazards. Depots and stops are points
placed around the live hazard (test scaffolding; real ones come from B's facilities data).
Needs TEST_DATABASE_URL and VALHALLA_URL.
"""

import os

import httpx
import pytest
from shapely import MultiPolygon

from app.core.migrate import apply_all
from app.core.resilience import CircuitBreaker
from app.routing.repository import fetch_active_hazards
from app.routing.valhalla import LocationNotRoutableError, NoRouteError, ValhallaClient
from app.solvers.geometry import exclusion_rings, verify_route
from app.solvers.vrp import VrpProblem, VrpStop, VrpVehicle, solve

from .live_sources import live_hazards, ring_points
from .test_integration_db import scratch_db  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration
VALHALLA_URL = os.environ.get("VALHALLA_URL")


async def test_live_hazard_through_full_routing_pipeline(scratch_db):  # noqa: F811
    if not VALHALLA_URL:
        pytest.skip("VALHALLA_URL not set")
    await apply_all(scratch_db)
    async with httpx.AsyncClient() as http:
        valhalla = ValhallaClient(VALHALLA_URL, http, breaker=CircuitBreaker("valhalla"))
        async for guid, sender, _, poly in live_hazards(http):
            points = ring_points(poly, offset_m=3000, count=6)  # 2 depots + 4 stops around the hazard
            try:
                await valhalla.route(points[:2])
            except (NoRouteError, LocationNotRoutableError):
                continue  # outside the routing graph's coverage
            break
        else:
            pytest.fail("no live hazard inside the routing graph")

        await scratch_db.execute(
            """INSERT INTO hazards (source, source_ref, dedupe_key, hazard_type, severity, headline, geom, fetched_at)
               VALUES ('sachet', $1, $1, 'other', 'severe', $2, ST_GeomFromText($3, 4326), now())""",
            guid, f"live SACHET alert from {sender}", MultiPolygon([poly]).wkt)
        hazards = await fetch_active_hazards(scratch_db)
        assert len(hazards) == 1

        rings = exclusion_rings(hazards)
        matrix = await valhalla.matrix(points, points, exclude=rings)
        depots, stops = [0, 3], [1, 2, 4, 5]  # opposite sides of the hazard
        problem = VrpProblem(
            durations_s=matrix.durations_s,
            vehicles=[VrpVehicle(vehicle_id=f"truck-{d}", start_node=d, end_node=d, capacity_kg=2000) for d in depots],
            stops=[VrpStop(stop_id=f"stop-{n}", node=n, demand_kg=600, service_s=600) for n in stops],
            horizon_s=12 * 3600, time_limit_s=2,
        )
        solution = solve(problem)
        print(f"\nlive hazard {guid} ({sender}); VRP status={solution.status}, unserved={solution.unserved}")
        assert solution.routes

        for route in solution.routes:
            legs = await valhalla.route([points[n] for n in route.nodes], exclude=rings)
            verdict = verify_route(legs.shape, hazards)
            print(f"{route.vehicle_id}: nodes {route.nodes}, load {route.load_kg:.0f} kg, "
                  f"{legs.distance_m / 1000:.1f} km, {legs.duration_s / 60:.0f} min, "
                  f"arrivals {[v.arrival_s // 60 for v in route.visits]} min, "
                  f"verdict={'PASS' if verdict.passed else 'FAIL'}, clearance {verdict.min_clearance_m:.0f} m")
            assert verdict.passed, verdict.explain()
            assert route.load_kg <= 2000
