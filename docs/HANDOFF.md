# ReliefRoute: handoff (status on 2026-10-31)

Start here when continuing on a new machine. The full rules are in `docs/MASTER_PROMPT.md`. Per-gate
details are in `docs/gates/` (A-G0, A-G1, A-G2, B-G1, B-G2).

## 1. Where things are

| Branch | Contents |
|---|---|
| `main` | Everything merged (same as `integration/gate2`, commit `a4d48d0`) |
| `feat/brain` | Instance A (Brain): agents, graph, solvers, routing, dispatch, API `/api/plans…` |
| `feat/senses-face` | Instance B (Senses & Face): ingestion, telemetry, portal, SSE, frontend, driver PWA, infra |
| `integration/gate2` | A + B merged; this is what was running |
| Tags | `phase0-contracts`, `phase1-routing` (A G1), `phase1-senses` (B G1). There is no Gate 2 tag yet (not fully verified). |

Ownership rules (see `contracts/ownership.txt`, checked by `contracts/tools/check_ownership.py`):
- A works on `feat/brain`, B on `feat/senses-face`.
- `contracts/` is frozen; changes go via `contracts/requests/` plus a numbered patch in `contracts/db/patches/`.
- Never force-push. Merge `main` into branches instead of rebasing.

## 2. Gate status

| Gate | A (Brain) | B (Senses & Face) |
|---|---|---|
| 0 Contracts & skeleton | ✅ done, verified on PostGIS/Redis | n/a |
| 1 | ✅ Valhalla client, OR-Tools VRP, Shapely verifier; a real route avoided a real SACHET hazard | ✅ live ingestion (SACHET, GDACS, Open-Meteo, OSM facilities), live map, drill tool |
| 2 | 🟡 built and tested; live run reached the approval gate (see §5) | 🟡 built and tested; no real phone GPS yet |
| 3 Closed loop + edge cases | ⬜ not started | ⬜ not started |
| 4 Polish, deploy, README | ⬜ not started | ⬜ not started |

Tests at the last run: **155 passed, 3 skipped** (the skips are SACHET-rate-limited live tests) on the merged code, against live PostGIS, Redis and Valhalla.

## 3. What exists (short)

**Contracts:**
- `contracts/db/schema.sql` (14 tables and views), plus patch 001: `places`/`v_places` and `POST /api/vehicles`.
- `events.md` (6 Redis Streams).
- `openapi.yaml`.
- The Python package `reliefroute_contracts`, imported by both sides.

**B: ingestion** (`backend/app/ingestion/`, run with `python -m app.ingestion.worker`):
- **SACHET (NDMA CAP):** national alerts. SACHET rate-limits polygon downloads (HTTP 403), so the fallback places alerts on OSM mandal/district boundaries using the names in `areaDesc` (unique matches only, labelled).
- **GDACS:** cyclone/flood polygons by alert level. An event expires 24 h after its last advisory.
- **Open-Meteo:** forecast rain classified with IMD 24 h categories.
- **NASA FIRMS:** code is ready; disabled until `FIRMS_MAP_KEY` is set.
- **OSM facilities:** Overpass, falling back to the local OSM extract (Overpass was down the whole time).
- **OSM places + census population** and admin boundaries, from the extract.
- **AePDS** (`aepos.ap.gov.in`): **real** live PDS rice and dal stock per mandal supply office, stored as verified government suppliers at a point inside the mandal (approximate: shop locations aren't published).

**B: other:**
- `POST /api/vehicles` and `POST /api/telemetry` (batched, idempotent).
- Supplier portal `/portal` (register, report stock); WhatsApp webhook (disabled until Twilio is configured).
- SSE `/api/stream`.
- **Frontend** (Next.js 16 + MapLibre): map, source freshness badges, drill tool, plans, approval panel, live agent timeline, fleet.
- **Driver PWA** at `/driver/`: GPS, offline queue, assignment, Telugu/Hindi voice.

**A** (`backend/app/agents`, `graph`, `llm`, `solvers`, `routing`, `dispatch`; run with `python -m app.graph.worker`):
- LangGraph with Postgres checkpointer: Sentinel (LLM triage), Needs (Sphere standards: water 15 L/p/d; rice 2,100 kcal as 0.575 kg/p/d), Procurement (nearest suppliers within 150 km, Valhalla matrix), Route Planner (OR-Tools pickup/delivery VRP), Critic (≤3 retries), Supervisor brief (LLM), approval gate (`interrupt`), Dispatcher (idempotent orders/routes, Telugu translation with a number-preservation check, PO PDFs, SMS/WhatsApp records).
- Gemini client: Pydantic repair-and-retry, plus a **model fallback chain** (models get retired or overloaded; startup probes pick the working ones). Labelled rule-based fallbacks apply when there's no LLM.
- Every step is logged to `agent_events` with source IDs.
- API: plans trigger/list/get/approve, orders, vehicle assignment, brain health; `application/problem+json` errors.

**Demo data:** `infra/demo/seed_demo_water.py` created **50,000 L of drinking water** at two OSM sites near Vijayawada, at the owner's request (there is no public water stock source). The suppliers are named "DEMO SEED (not real stock)" and are unverified. ⚠️ This conflicts with the hackathon zero-fake-data rule: replace it with a real check-in before judging, or keep it clearly disclosed.

## 4. Set up on a new machine

Prerequisites: Docker Desktop (give it 6–8 GB RAM), Python 3.12 + `uv`, Node 22, `osmium-tool` (`brew install osmium-tool`), and `cloudflared` (for phone testing).

```bash
git clone https://github.com/Dracula-5/SafePassage.git && cd SafePassage   # main has everything

# 1. Map data (~10 min). Data lives outside the repo in ~/reliefroute-data
infra/valhalla/build.sh                      # Geofabrik southern-zone -> clip to Andhra Pradesh
infra/osm/extract_facilities.sh
infra/osm/extract_places_admin.sh

# 2. Services: PostGIS :55432, Redis :6380, Valhalla :8002
docker compose --profile routing up -d       # first Valhalla start builds tiles (~5 min)
python3 infra/valhalla/raise_limits.py && docker compose restart valhalla   # REQUIRED

# 3. Backend (one venv for both sides)
cd backend
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ../contracts/python -r requirements/brain.txt -r requirements/senses.txt -r requirements/dev.txt
cp ../env/senses.env.example .env
# then append from env/brain.env.example: GEMINI_API_KEY=<your key>, LLM_PROVIDER=gemini, VALHALLA_URL=http://localhost:8002
.venv/bin/python -m app.core.migrate
.venv/bin/uvicorn app.main:app --port 8000          # API + /portal + /driver/
.venv/bin/python -m app.ingestion.worker            # separate terminal (AePDS download takes ~5-10 min)
.venv/bin/python -m app.graph.worker                # separate terminal

# 4. Frontend
cd ../frontend && npm install && cp .env.example .env.local && npm run dev   # http://localhost:3000

# 5. Tests
cd ../backend
TEST_DATABASE_URL=postgresql://reliefroute:reliefroute@localhost:55432/postgres \
TEST_REDIS_URL=redis://localhost:6380/15 VALHALLA_URL=http://localhost:8002 \
.venv/bin/python -m pytest -q
```

Notes:
- If host port 55432 or 6380 is busy, change it in `docker-compose.yml` and the `.env` files.
- Phone testing needs HTTPS: `cloudflared tunnel --protocol http2 --url http://localhost:8000`, then open `https://<tunnel>/driver/` on the phone. Use `http2` because some networks block QUIC. Stop the tunnel afterwards, because the API has no auth.

## 5. Last live run (Gate 2 evidence so far)

The drill was published over Vijayawada. The vehicle was the real driver app in Chrome **with a simulated location** (labelled `TEST-SIM-GPS`), because the owner was outside Andhra Pradesh. Results:
- Live Gemini triage: "critical, 72 h", and it flagged the 1% census coverage.
- 7 orders across 6 suppliers (real PDS rice plus the demo water).
- 1 route; the Critic passed it on the first attempt.
- The LLM brief listed every approval reason.
- The plan stopped at `awaiting_approval`. **It was never approved**, so dispatch, the driver-app assignment and Telugu voice are only proven in automated tests on real PostGIS and Valhalla, not live.

## 6. To do next

### Finish Gate 2
1. Run a drill, register a vehicle in the driver app, and approve on the dashboard. Then verify:
   - orders and routes are written;
   - the driver app shows the route and reads Telugu aloud;
   - PO PDFs are in `~/reliefroute-data/documents/`.
2. Real-phone GPS: a teammate **inside Andhra Pradesh** opens the tunnel URL. The road graph covers AP only, and vehicles more than 200 km from the relief points are ignored.
3. Replace or disclose the demo water seed (see §3).
4. Write the A-G2/B-G2 addenda with that output, then tag `phase2-…`.

### Known issues to fix (found in live runs)
- **Population:** OSM census tags cover about 2.7% of settlements, and a whole city's figure counts when its centre falls in the zone. Vijayawada becomes 1,048,240 people, so demand of about 1,135 t is unrealistic. Consider WorldPop (needs a contract patch) or scaling by overlap with the city boundary.
- **Single-truck VRP:** with huge demand, one truck gets a 72 h, ~1,725 km shuttle route. Cap trips per vehicle, or plan only the first wave.
- **Stale approvals:** approving after the hazard expired still dispatches. Re-check hazards and stock at approval and resume time.
- The SSE stream doesn't emit `plan` events (the UI polls A's API on agent events).
- Langfuse tracing isn't wired (no keys). Twilio isn't configured, so messages are recorded as "not sent".
- 186 AePDS supply offices are skipped because their names don't match an OSM mandal. Add an alias table.
- Next 16: MapLibre's worker is copied to `frontend/public/maplibre` on `npm install` (postinstall script); don't remove it.

### Gate 3 (closed loop + robustness)
- A: Replanner that consumes `vehicle.telemetry`; detects off-route, stalled and offline drivers and hazard growth; emits `vehicle.deviation`; replans and reassigns.
- Edge cases with demos: no safe route (propose an alternate hub), supplier shortage (split/escalate, mostly done), malformed LLM output (done), duplicate alert/dispatch (done), driver offline (reassign), stale data (badges and fallbacks, mostly done).
- Chaos tests: `backend/tests/brain/chaos/`, `backend/tests/senses/chaos/` (kill or slow Valhalla, Redis, Gemini, SACHET).
- B: reconnecting SSE UI states, PWA offline queue tests, input-validation sweep.

### Gate 4 (polish, deploy, docs)
- Deploy the backend (Fly.io/Railway; it needs PostGIS, Redis and Valhalla tiles) and the frontend (Vercel).
- Root `README.md` with setup, a Mermaid architecture diagram and a data-source table; `docs/brain.md` (graph diagram); `docs/demo-script.md` with a fallback plan for when live sources are down.
- Final runtime audit: `python contracts/tools/audit_runtime.py`.

## 7. Facts worth knowing
- Demo region: **Andhra Pradesh**. SACHET is busiest there (AP SDMA lightning and flood alerts).
- **Never burst SACHET polygon downloads.** It blocks the IP (403) for hours. The poller is throttled and has the area-name fallback.
- Gemini: `gemini-2.5-flash` is retired (404), and several 3.x models are often overloaded. The fallback chain handles this; `LLM_MODEL` can pin one.
- AePDS responses are about 50 MB and can take minutes. One commodity at a time, every 6 h.
- Valhalla limits that matter: 400 km per matrix pair, 2,500 pairs, 20 locations per route, and the exclusion-polygon perimeter (raised to 5,000 km by `raise_limits.py`). Hazard exclusions are filtered to the trip area for this reason.
