# B-G1: Live data and live map (Instance B, Senses & Face)

Branch `feat/senses-face`, based on `main` (contracts plus the approved `v_facilities`/`v_suppliers` views).
Work happens in its own worktree (`~/Desktop/SafePassage-B`) so it never touches A's checkout.

## What was built
**infra/** (B owns these files)
- `docker-compose.yml` (project `reliefroute`): PostGIS 16/3.4 on host port **55432**, Redis 7 on **6380**, Valhalla on **8002** (`--profile routing`). Host 5432/5433 are taken by a local Postgres.
- `infra/valhalla/build.sh` downloads Geofabrik `southern-zone`, checks its md5, and clips it to Andhra Pradesh. `raise_limits.py` raises Valhalla's exclude-polygon limits, which is required. All steps are documented in `infra/README.md`.
- `infra/osm/extract_facilities.sh` cuts relief facilities out of the same OSM extract (36,679 features, data as of 2026-10-29T21:21:26Z) as the **fallback** when Overpass is down.

**backend/app/ingestion/** (deterministic, no LLM)

| Source | What becomes a hazard / facility | Failure handling |
|---|---|---|
| NDMA SACHET (CAP 1.2) | Every alert that has a polygon (inline or from SACHET's polygon file). Event text maps to a hazard type; CAP severity is kept. Alerts with no polygon are skipped; an extent is never invented. Duplicate re-issues collapse via `dedupe_key` (sender + event + area + validity). `msgType=Cancel` cancels the hazard. | Breaker. Polite: 2 concurrent requests, 0.5 s pause, at most 25 new alerts per poll. If every download fails, the source is degraded and the breaker opens for 15 min. |
| GDACS | One hazard per (event, alert level) from GDACS impact polygons (Red, Orange, Green map to extreme, severe, moderate). The event ends 24 h after its last advisory, because GDACS leaves `iscurrent=true` for days. | Breaker. Events without a polygon are skipped. |
| Open-Meteo | Forecast daily rain on a 0.5° grid over the region. Cells reaching IMD's 24 h categories (64.5 / 115.6 / 204.5 mm) become `heavy_rain` hazards for that day. | Breaker. This also complements SACHET's IMD alerts. |
| NASA FIRMS | VIIRS detections become pixel-footprint hazards (scan × track). | **Disabled until `FIRMS_MAP_KEY` is set.** Nothing is invented. |
| OSM facilities | Overpass (2 mirrors), with the OSM extract as fallback. Covers hospital/clinic, school, shelter, warehouse and fuel; named features only. | The fallback is recorded in `source_health.fallback_source` and shown as an amber badge. |

- `store.py` upserts by `dedupe_key`. A content hash means `version` and `hazard.updated` only change when the alert really changes. Events are published after commit. An expiry sweep runs every 60 s.
- `runner.py` writes failures to `source_health`. `v_source_freshness.is_stale` drives the "DATA STALE" badges. Existing rows are kept when a source fails.
- `worker.py`: `python -m app.ingestion.worker` (or `--once`).
- Migration `201_hazard_content_hash.sql` (additive).

**backend/app/api_senses/** implements the contract: `GET /api/hazards` (active, drill and bbox filters; `is_stale` per source), `GET /api/facilities` (kind and bbox), `GET /api/vehicles`, `POST /api/drill` (validated polygon, area cap, `source=drill`, headline "DRILL: …", emits `hazard.updated`), `DELETE /api/drill/{id}`, `GET /api/sources/health`.

**frontend/** (Next.js 16 + MapLibre 6)
- Live map: hazards coloured by severity; drills drawn with a purple dashed outline and a DRILL marker; facilities loaded for the current view from zoom 9.
- Sidebar: per-source freshness, with fallback, "DATA STALE" and breaker badges plus the last error; the active hazard list.
- Drill tool: click corners on the map, then label, type, severity and TTL, then publish. Drills are ended from their popup.
- Polls every 30 s (SSE arrives at Gate 2).
- MapLibre's worker is served from `public/` because Next's bundler can't resolve it (`scripts/copy-maplibre-worker.mjs` runs on postinstall).

## Real output (2026-10-30, live sources)
```
$ curl localhost:8000/health
{"status":"ok","checks":{"postgres":"ok","redis":"ok"}}

$ curl localhost:8000/api/sources/health        (abridged)
gdacs      closed  ok 16:14:53Z  stale=false
open_meteo closed  ok 16:14:55Z  stale=false
overpass   closed  ok 16:18:58Z  fallback=osm_extract:facilities.geojsonseq  error="used fallback ..."
sachet     closed  last_error="SourceUnavailable: sachet: all 25 alert downloads failed, e.g. polygon 1793376744682008: HTTP 403"

$ curl localhost:8000/api/hazards               (first pass)
13 active hazards
  gdacs extreme cyclone | Tropical Cyclone ARNAB-26 (red zone)
  gdacs severe  cyclone | Tropical Cyclone ARNAB-26 (orange zone)
  gdacs moderate cyclone| Tropical Cyclone ARNAB-26 (green zone)
  open_meteo moderate heavy_rain | Forecast heavy rainfall: 100 mm on 2026-11-01 (IMD category)   (+9 more cells)
```
ARNAB-26's last advisory was on 2026-10-28T06:00, yet GDACS still said `iscurrent=true` and the green zone covered about 370,000 km². After the fix (expiry = last advisory + 24 h) the sweep expired it:
```
gdacs|expired|3|2026-10-29 06:00:00+00     (x3, version 3)
$ curl localhost:8000/api/hazards  ->  10 active (open_meteo: 10)
```
The Open-Meteo cells currently over AP's region box are all offshore in the Bay of Bengal (83–85°E, 12.5–14.5°N). That is real marine forecast data. It doesn't affect road routing.

**Drill driven through the real UI** (Chrome via DevTools protocol: click "Draw drill zone", type a label, click 4 map corners, "Publish drill"):
```
message: open_meteo: 10 · drill: 1 | Drill hazard created and published (hazard.updated).
drill labels on map: 1
API:   DRILL: Krishna river embankment breach near Vijayawada | severe flood drill:e19f3418-… | expires 2026-10-30T18:33:01Z
Redis: {"event_type":"hazard.updated","producer":"senses","payload":{"hazard_id":"d8ca0b70-…","change":"created","source":"drill",...}}
```
**Facilities:** `GET /api/facilities?bbox=80.5,16.4,80.7,16.6&kind=hospital,shelter,school` returned 650 (526 hospital, 122 school, 2 shelter). Zoomed into Vijayawada, the map showed "1276 facilities in view".

Screenshots (local, not committed: B's ownership covers `docs/gates/B-*.md` only) are in the session scratchpad: `b-g1-map.png` (statewide view, source badges, rain cells), `b-g1-drill.png` (drill with DRILL label), `b-g1-facilities.png` (zoomed facilities inside the drill).

## Tests
```
$ TEST_DATABASE_URL=postgresql://reliefroute:reliefroute@localhost:55432/postgres TEST_REDIS_URL=redis://localhost:6380/15 \
  .venv/bin/python -m pytest -q -rs tests/senses
48 passed in 20.67s
```
- **Parsers:** run on real responses captured on 2026-10-30 (`tests/senses/fixtures/`): SACHET RSS/CAP/polygon, GDACS events plus TC geometry, Open-Meteo.
- **Rules:** IMD thresholds, GDACS expiry, SACHET dedupe/cancel, rate-limit degradation, malformed polygons.
- **Integration (PostGIS + Redis):**
  - upsert versioning and duplicate suppression;
  - the expiry sweep;
  - the runner publishing `hazard.updated` and recording health on failure;
  - the full drill lifecycle with stream events (`created` then `cancelled`);
  - bad drill geometry rejected with 422 (bow-tie, point, oversized);
  - cancelling a non-drill returns 404;
  - facility filters.
- Frontend: `tsc --noEmit` and `eslint` are clean.

## Self-audit
- (a) `audit_runtime.py` over backend/app, frontend and driver-pwa: **0 findings**. The region box lives only in env/config. Test fixtures are only under `tests/`.
- (b) `check_ownership.py --role B --base main`: **OK: all changed files belong to role B**.
- (c) Secret scan is clean. `backend/.env` and `frontend/.env.local` are ignored; only `.env.example` files are committed.
- (d) Every outbound call goes through `resilient_request` (timeout, retry, breaker) and has a failure path into `source_health`.
- (e) Every record is a validated Pydantic model (`NormalizedHazard`, `NormalizedFacility`, request bodies).

## Known gaps (honest)
1. **No SACHET alerts are in the database yet.** The first pass fetched about 100 polygon files quickly, and SACHET then rate-limited this IP: `FetchPolygonXMLFile` returns HTTP 403 for any user agent, while the CAP endpoint still answers 200. The poller now degrades correctly (red "DATA STALE" in the UI) and backs off. The worker is running and will ingest once the block lifts. Proposed fallback (needs your OK): resolve CAP `areaDesc` mandal and district names to official OSM admin boundaries from the extract.
2. Overpass mirrors timed out or returned 502 during the whole session. Facilities come from the OSM extract fallback, which is marked as fallback in the UI.
3. **FIRMS** needs a free `FIRMS_MAP_KEY`. **data.gov.in FCI godowns** need an API key, and I haven't confirmed that the dataset has coordinates. Both are off, with no stand-in data.
4. **CWC / India-WRIS** has no documented public API. CWC flood alerts already arrive through SACHET (5 of 100 recent items). **IMD** has no open API; its warnings come via SACHET, and Open-Meteo is classified with IMD thresholds.
5. **Error format:** endpoints return FastAPI's `{"detail": ...}`, not the contract's Problem schema. The fix belongs in `app/main.py` (A).
6. OSM has only a handful of cyclone shelters mapped for AP. Schools are included as the usual designated relief camps. `kind=hospital` also includes clinics (`attributes.amenity`).
7. The driver PWA isn't started; it's Gate 2 scope.

## Questions for the human
1. FIRMS MAP_KEY and data.gov.in API key? (Optional sources.)
2. Approve the SACHET fallback: `areaDesc` mapped to OSM admin boundaries?
3. **Population** for A's Needs agent: no source is wired yet. Options are the Census 2011 `population` tags on OSM `place=*` nodes (present for many AP villages) or WorldPop rasters. That would be a new B table, so it needs a contract request. Which do you prefer?
4. GitHub's default branch is probably `feat/brain`; switch it to `main`.
