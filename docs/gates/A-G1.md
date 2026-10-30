# A-G1: Standalone routing and solvers (Instance A, Brain)

Branch `feat/brain`. Gate 1 builds on Gate 0 (`phase0-contracts`) and does not depend on any of B's code.

## What was built
| Module | What it does |
|---|---|
| `app/routing/valhalla.py` | Async Valhalla client: `route` (multi-stop, polyline6 decoded to lon/lat), `matrix` (`sources_to_targets`), both with `exclude_polygons`. Every call goes through `core.resilience` with a 15 s timeout, 2 jittered retries and a circuit breaker. Valhalla "no path" (442/443) becomes `NoRouteError` and "no road near location" (170/171/158) becomes `LocationNotRoutableError`, so the planner can mark a destination unreachable instead of crashing. Non-JSON errors are typed too. |
| `app/routing/polyline.py` | Encoded-polyline decoder. Checked against Google's reference example and a precision-6 round trip. |
| `app/routing/repository.py` | Reads B's data **through the contract views only**: `v_active_hazards` (optional bbox, drills on or off) and `v_vehicle_status`. |
| `app/solvers/geometry.py` | Shapely, working in metres in a local UTM projection. `exclusion_rings` grows each hazard by a margin and simplifies below that margin, so the result always still contains the hazard. Overlapping hazards are merged. It respects a vertex budget, and its last resort (convex hull) still covers the hazard. `verify_route` returns `intersects` (with metres inside the hazard) or `too_close` (with clearance) violations, plus human-readable `explain()` lines for the Critic. |
| `app/solvers/vrp.py` | OR-Tools multi-depot capacitated VRP with time windows: per-vehicle depots, capacity in kg, stop time windows, service times, vehicle availability windows, and `None` matrix cells for pairs with no safe route. Stops that can't be served are dropped at a large penalty and reported in `unserved` (Gate 3 edge cases build on this). Only depot and stop nodes enter the model. |
| `contracts/requests/A-facilities-suppliers-views.md` | **Contract gap that I own (from Gate 0):** there is no view for `facilities` or `suppliers`. It proposes additive `v_facilities` and `v_suppliers`. Until it's approved, A code does not read those tables. |

## Live evidence: a real route that avoids a real hazard polygon
- **Hazard:** a live NDMA SACHET alert fetched during the test, `1793374599788008` from Andhra Pradesh SDMA (lightning warning, 708-vertex mandal polygon in Palnadu district).
- **Roads:** self-hosted Valhalla 3.9.1 built from the OSM Geofabrik `southern-zone` extract, clipped to Andhra Pradesh.
- **Endpoints:** derived from the polygon itself, 4 km beyond its west and east edges.
```
$ VALHALLA_URL=http://localhost:8002 pytest -s tests/brain/test_integration_routing.py
SACHET alert 1793374599788008 from controlroom@ndma.gov.in (Andhra Pradesh SDMA): మీ ప్రాంతంలో పిడుగులు పడే అవకాశం ఉంది...
hazard polygon: 708 vertices, bounds (79.324, 16.2294, 79.6429, 16.4854)
origin 16.35598,79.28544 -> destination 16.35883,79.68122
UNCONSTRAINED: 74.9 km, 95 min, verdict=FAIL ['route enters hazard sachet:1793374599788008 for 22037 m']
HAZARD-AWARE : 95.0 km, 112 min, verdict=PASS, min clearance 1620 m, exclusion ring vertices [93]
matrix time 6733 s vs route time 6734 s
2 passed in 1.80s
```

**Full Gate 1 pipeline on the same live hazard.** The hazard is written into a scratch PostGIS database, then read back through `v_active_hazards`. From there: exclusion rings, then the Valhalla matrix, then the OR-Tools VRP (2 depots, 4 stops, 2000 kg trucks), then real Valhalla routes per truck, then the Shapely check:
```
$ TEST_DATABASE_URL=... VALHALLA_URL=... pytest -s tests/brain/test_integration_pipeline.py
live hazard 1793374599788008 (controlroom@ndma.gov.in (Andhra Pradesh SDMA)); VRP status=success, unserved=[]
truck-0: nodes [0, 5, 4, 1, 0], load 1800 kg, 190.3 km, 239 min, arrivals [52, 119, 222] min, verdict=PASS, clearance 1620 m
truck-3: nodes [3, 2, 3], load 600 kg, 86.2 km, 107 min, arrivals [53] min, verdict=PASS, clearance 1620 m
1 passed in 4.38s
```

## Full suite (all live services connected)
```
$ TEST_DATABASE_URL=postgresql://reliefroute:reliefroute@localhost:55432/postgres \
  TEST_REDIS_URL=redis://localhost:6380/15 VALHALLA_URL=http://localhost:8002 \
  .venv/bin/python -m pytest -q -rs
70 passed, 1 warning in 18.14s
```
New unit tests:
- Geometry (8): intersect, clearance, pass, merge, vertex budget still covers.
- VRP (8): capacity split, time-window ordering, unreachable and oversized stops reported, multi-depot, vehicle availability, invalid input.
- Valhalla client (9): request body and units, null matrix cells, typed error codes, retry then open breaker, non-JSON errors.
- Repository (1 integration): expired, cancelled and drill filtering, bbox filter, offline vehicle.

## Self-audit
- (a) `audit_runtime.py`: 0 findings. Runtime code has no coordinates. Points in tests are either derived from the live polygon or are local test frames.
- (b) `check_ownership.py --role A --base phase0-contracts`: "OK: all changed files belong to role A".
- (c) No secrets. `backend/.env` holds the Gemini key slot and is git-ignored.
- (d) Valhalla: timeout, retry, breaker and typed failures. The test's SACHET calls have timeouts and skip on failure.
- (e) Every solver input and output is a Pydantic model (`VrpProblem` validates the matrix shape, node ranges and duplicates).

## How Valhalla was set up (B owns infra; these commands are for B's compose)
```
curl -L -o southern-zone-latest.osm.pbf https://download.geofabrik.de/asia/india/southern-zone-latest.osm.pbf   # md5 verified
osmium extract --bbox 76.6,12.5,84.9,20.1 --strategy complete_ways -o valhalla/andhra-pradesh.osm.pbf southern-zone-latest.osm.pbf  # 269 MB
docker run -d --name rr-valhalla -p 8002:8002 -v ~/reliefroute-data/valhalla:/custom_files \
  -e serve_tiles=True -e build_admins=True -e build_time_zones=True -e use_tiles_ignore_pbf=True \
  -e server_threads=4 ghcr.io/valhalla/valhalla-scripted:latest          # 579 tiles, about 5 min, peak about 1.1 GB RAM
```
**Required config change:** set `service_limits.max_exclude_polygons_length = 5000000` and `max_exclude_polygons_vertices = 10000` in `valhalla.json`, then restart. The defaults (10 km total perimeter, 100 vertices) reject any real hazard zone. B's compose should mount the same `~/reliefroute-data/valhalla` directory so the tiles aren't rebuilt.

## Known gaps
- `facilities` and `suppliers` can't be read until the contract request is decided. Gate 1 itself doesn't need them.
- The VRP assumes each stop can be served in one visit (no split deliveries). Splitting is Procurement and Route Planner logic in Gate 2/3.
- The route shape is checked against hazards as they are at planning time. Hazard growth is the Replanner's job in Gate 3.
- Large-instance performance hasn't been tuned. The solver has a time limit, 5 s by default.
- No `main` branch on the remote, so there was nothing to rebase onto.

## Questions for the human
1. Approve `contracts/requests/A-facilities-suppliers-views.md`?
2. Create `main` on GitHub from `phase0-contracts` (B's worktree is branched from that tag).
3. Before Gate 2: confirm the Gemini key is in `backend/.env`. The model id gets verified against the live API in Gate 2.
