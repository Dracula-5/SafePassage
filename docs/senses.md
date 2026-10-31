# Senses and Face

How data gets in and what people see. Code: `backend/app/ingestion`, `telemetry`, `suppliers`, `api_senses`, `frontend/`, `driver-pwa/`.

## Ingestion

Every source is a poller with a timeout, retries with jitter, and a circuit breaker. One poll is one transaction: fetch, upsert, publish events, record health. There is no LLM on this side.

| Source | Every | Writes | Fallback |
|---|---|---|---|
| SACHET (NDMA CAP) | 2 min | `hazards` | Rate-limited polygon downloads: place the alert on OSM boundaries by its `areaDesc`, unique matches only, labelled |
| GDACS | 15 min | `hazards` | None; existing rows stay |
| Open-Meteo | 3 h | `hazards` (IMD 24 h categories) | None; existing rows stay |
| NASA FIRMS | 30 min | `hazards` | Off without `FIRMS_MAP_KEY` |
| OSM facilities | 24 h | `facilities` | Overpass mirrors, then the local extract |
| OSM places and boundaries | 24 h | `places`, `admin_areas` | Local extract only |
| AePDS | 6 h | `suppliers`, `stock` | None; existing rows stay |

**Failure policy.** A failed poll changes no data. `source_health` records the error and breaker state, and `v_source_freshness` turns `is_stale` once the TTL passes, which drives the `DATA STALE` badge. A failed poll retries after 2 minutes rather than waiting a full interval. The first AePDS poll on a new database fails until boundaries load, and used to wait 6 hours.

**Deduplication.** Hazards have a `dedupe_key` and a content hash. A repeat of the same alert changes nothing and emits nothing; `version` moves only on real changes.

**Matching AePDS offices to mandals.** The two sources romanise Telugu names differently, and a wrong match puts real stock in the wrong town. An office must match a spelling-insensitive key, inside the districts that its neighbours' exact matches point to, with one answer left. Anything else is skipped and logged by name. Plain fuzzy matching was tried and rejected: it sent Kovur to Kovvur and Bogole to Ongole.

**Never burst SACHET polygon downloads.** It blocks the IP with 403s for hours. The poller is throttled.

## Telemetry

`POST /api/telemetry` takes batches so a phone can flush its offline queue. `(vehicle_id, recorded_at)` is unique, so a resend counts as a duplicate and is never emitted twice. Each new fix is published to `vehicle.telemetry`, oldest first. Fixes dated in the future or more than two days old are refused.

"Offline" is not stored: `v_vehicle_status` derives it from `last_seen_at`.

## Suppliers

The portal at `/portal` lets a supplier register at an existing OSM facility or at their GPS position, then report stock with a token. WhatsApp check-in works through a Twilio webhook, off until `TWILIO_AUTH_TOKEN` is set, with signatures verified.

Stock numbers come only from a supplier or AePDS. New suppliers start unverified, and the approval gate says so.

## The live stream

`GET /api/stream` is Server-Sent Events.

| Event | Contains |
|---|---|
| `agent_event` | Timeline rows; the SSE id is the row id, so `Last-Event-ID` resumes with no gaps |
| `plan` | Plan status changes |
| `hazard` | `hazard.updated` payloads |
| `vehicle` | Position or liveness changes |
| `source_health` | Freshness or breaker changes |
| `error` | Postgres or Redis hiccup; the stream stays open |

## Dashboard

Next.js and MapLibre. Live hazards, facilities and vehicles, source freshness, the drill tool, plans, the approval panel and the reasoning timeline.

When the stream drops, the header shows "reconnecting", a banner warns the screen may be out of date, and everything is reloaded on reconnect. If the backend is unreachable, the left panel says so.

## Driver PWA

Served from the same origin at `/driver/`, so it works through one tunnel or domain.

- Fixes are written to a local queue first and uploaded every 15 s. A dead zone loses nothing, and the queue holds about a day of fixes. See `driver-pwa/queue.js`.
- The app shell is cached, so it opens without signal.
- The assignment refreshes every 20 s with stop progress. A replacement route shows a banner and is read aloud.
- Voice uses the phone's Web Speech voices, in Telugu, Hindi or English.

## Input limits

| Input | Limit |
|---|---|
| Drill | Valid polygon with area, up to 1,000 vertices and 50,000 km², inside the region; expires in 5 to 1,440 minutes |
| Telemetry | Up to 500 fixes per batch; coordinates, speed, heading and accuracy in range; timezone required |
| Vehicle | E.164 phone, capacity up to 60 t |
| Facilities query | Up to 5,000 rows |
| Stock report | One line per item |

`OPERATOR_TOKEN` protects drills, plans, approvals and operational reads, and `ENROLMENT_CODE` protects vehicle registration. Device tokens and supplier tokens are stored only as hashes. See [security.md](security.md).
