# B-G2: Telemetry, suppliers, stream (Instance B, Senses & Face)

Branch `feat/senses-face` (merged with `main` for contract patch 001). Run together with A in the
local-only `integration/gate2` worktree.

## What was built
- **Contract patch 001** (approved by you): a B-owned `places` table plus `v_places` (census population from OSM), and `POST /api/vehicles`.
- **Populated places:** 67,352 settlements, of which 1,823 have a census figure, loaded from the OSM extract (`infra/osm/extract_places_admin.sh`, data as of 2026-10-29).
- **SACHET fallback** (approved by you). When SACHET refuses polygons (HTTP 403 rate limit), it pauses polygon requests for 30 minutes and places each alert on the official OSM boundaries of the mandals or districts named in CAP `areaDesc`:
  - matching is unique-only, so an ambiguous name is skipped, not guessed;
  - candidates are limited to the sender's state;
  - every such hazard is labelled `geometry_source=areaDesc_osm_admin`, with the matched and unmatched names in its description.

  Live result: 13 real SACHET alerts were placed on mandal boundaries, for example "karempudi, durgi, dachepalle, machavaram, piduguralla". Alerts that can't be placed are skipped, and the source shows "fallback", not "stale".
- **Telemetry:** `POST /api/vehicles` returns the device token once and stores only its hash. `POST /api/telemetry` takes batches of up to 500 fixes, authenticates the device, ignores resends via (vehicle, recorded_at), rejects fixes stamped in the future or more than 2 days old, publishes `vehicle.telemetry` oldest first, and updates the last position. "Offline" is derived (120 s).
- **Supplier portal (`/portal`):** a phone-friendly page to register (at an OSM facility, or at the phone's GPS position as a new `portal` site) and report stock. `POST /portal/api/stock` (contract) upserts stock and publishes `supplier.stock.updated` with the previous quantity. **New suppliers are unverified**, and A asks for approval because of that. The WhatsApp webhook `POST /portal/whatsapp` checks Twilio's HMAC signature and is **disabled (503) until `TWILIO_AUTH_TOKEN` is set**, so it can't be spoofed.
- **SSE `GET /api/stream`:** sends `agent_event` (tailed by id, `Last-Event-ID` resume, a 200-event backlog on fresh connections), `hazard` (Redis `hazard.updated`), `vehicle` and `source_health` changes, keepalives, and an `error` event on dependency hiccups.
  - Deviation: it does **not** send `plan`. Plans are A's table with no contract view, so the UI refreshes them from A's `GET /api/plans` whenever an agent event arrives.
- **Frontend:**
  - three columns: situation, map, operations;
  - live agent timeline over SSE with a connection indicator;
  - plans list and plan detail (triage, Critic history, escalations, routes and clearance);
  - **approval panel** that calls A's `POST /api/plans/{id}/approve` with the approver's name;
  - fleet list; vehicles and planned routes on the map.
- **Driver PWA (`driver-pwa/`, served at `/driver/`, same origin as the API):**
  - registration and real GPS (`watchPosition`, at most one fix per 10 s);
  - offline queue in localStorage, flushed in batches every 15 s and safe to resend;
  - assignment from A's endpoint every 20 s;
  - instructions read aloud in Telugu or Hindi via the Web Speech API;
  - service worker for the app shell;
  - path-traversal-safe file serving.

## Evidence
```
$ cd backend && TEST_DATABASE_URL=... TEST_REDIS_URL=... pytest -q tests/senses
70 passed in 22.12s
$ (merged A+B) pytest -q  ->  155 passed, 3 skipped (SACHET 403)
```
New B tests:
- **Contract coverage:** every `senses` path in `openapi.yaml` is served.
- **Telemetry:** registration stores only a hash; a bad token gets 401; 3 fixes give 3 events in order; a resend counts 0 accepted and 3 duplicates; future fixes get 422.
- **Portal:** facility search by distance; register; stock events including `previous_quantity`; duplicate items get 422; a bad token gets 401; GPS sites; "either facility or GPS" is enforced.
- **WhatsApp:** 503 without Twilio; signature scheme; message parsing.
- **SSE:** order and resume from `Last-Event-ID`.
- **Driver app:** path traversal blocked.
- **SACHET fallback:** name normalisation; unique, ambiguous and state-filtered matching on PostGIS; a 403 leads to the cooldown, with exactly one polygon request and then fallback hazards.

Live: the SACHET fallback ingested 13 real alerts (`sachet_area_fallback` log lines quoted in the A-G2 timeline context). The live A+B run is in A-G2: a drill published through `POST /api/drill`, auto-planned by A, streamed to the dashboard and shown with escalations (screenshot `b-g2-ops.png`).

## Self-audit
(a) 0 findings. (b) `check_ownership --role B`: OK. (c) Secret scan clean; tokens hashed. (d) All outbound calls go through `resilient_request`. (e) Every endpoint body is a Pydantic model.

## Riskiest rule area (as the brief requires me to flag)
**Stock must come from real check-ins.** The system has no stock of its own, and today the database holds **zero** stock rows, so every plan correctly escalates. For the demo, someone has to enter genuine figures (a real supplier, or what a team member really holds). If anyone types made-up numbers, the zero-tolerance rule is broken by a human rather than by the code. I recommend the demo script states who the supplier is and what they hold.

## Not yet done or verified
1. **No real phone has sent GPS yet.** Phone geolocation requires HTTPS, so the phone needs a tunnel URL. That's waiting on your OK (see A-G2, question 3).
2. WhatsApp check-in is untested live (no Twilio).
3. Overpass was still down; facilities come from the OSM extract fallback, shown as amber in the UI.
4. The PWA's Telugu voice depends on the phone having a te-IN voice installed.

## Addendum, 2026-10-31: Driver PWA through a real dispatch

The real Driver PWA, in real Chrome with an emulated location, registered through its form, uploaded a fix, received a dispatched route and showed it with stop deadlines and the voice button, with no JavaScript errors. Evidence in `B-G3.md`.

Still open: a real phone inside Andhra Pradesh, and Telugu voice.
