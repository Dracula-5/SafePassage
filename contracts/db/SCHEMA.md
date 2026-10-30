# Database contract — FROZEN after Gate 0

Postgres 16 + PostGIS 3.4. All geometries are SRID 4326. `app.core.migrate` applies:

1. `contracts/db/schema.sql` — the frozen baseline (extensions, enums, every table, views)
2. `contracts/db/patches/*.sql` — approved contract changes, in number order (never edit an applied one)
3. `backend/migrations/*.sql` by file name — owners' additive changes (A: `1xx_*`, B: `2xx_*`)

## Ownership
| Owner | Tables |
|---|---|
| A (Brain) | plans, routes, orders, order_lines, approvals, notifications, agent_events, LangGraph checkpoint tables (created by `AsyncPostgresSaver.setup()`) |
| B (Senses) | hazards, facilities, suppliers, stock, vehicles, telemetry, source_health, places (patch 001) |

## Contract columns
Every column in `schema.sql` is a contract column: it may not be dropped, renamed or retyped. Owners may add nullable columns or columns
with defaults, indexes, and private tables.

## Semantics worth knowing
- `hazards.dedupe_key` collapses the same alert arriving from several polls/sources. Updating
  an existing hazard increments `version` and emits `hazard.updated`.
- Drill hazards: `source='drill'` and `source_ref LIKE 'drill:%'` (enforced by a CHECK).
- `stock` is a snapshot (one row per supplier x item). `v_available_stock.available` subtracts
  quantities in open orders, and in delivered orders newer than the stock report.
- `stock.unit_weight_kg` is declared by the supplier; the solver uses it for vehicle capacity.
- Vehicle "offline" is derived (`v_vehicle_status.is_offline`, 120 s since last fix).
- `agent_events` is append-only. B's SSE endpoint tails it by `id`.
- `source_health` covers B's ingestion sources; A reports its own dependencies (Valhalla,
  LLM, Twilio) via `GET /api/brain/health` and `data_stale` agent events.
- `v_places.population` is a lower bound (patch 001): most OSM settlements carry no census figure.
  Anything that sums it must also report coverage (places with population / all places in the area).
