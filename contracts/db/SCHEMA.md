# Database contract — FROZEN after Gate 0

Postgres 16 + PostGIS 3.4. All geometries are SRID 4326. Migrations apply in this order
(lexical file name across both dirs, see `app.core.migrate`):

1. `contracts/db/000_contract_types.sql` — extensions, enum types
2. `backend/migrations/1xx_*.sql` — A tables
3. `backend/migrations/2xx_*.sql` — B tables
4. `contracts/db/900_contract_views.sql` — cross-owner FKs, views

## Ownership
| Owner | Tables |
|---|---|
| A (Brain) | plans, routes, orders, order_lines, approvals, notifications, agent_events, LangGraph checkpoint tables (created by `AsyncPostgresSaver.setup()`) |
| B (Senses) | hazards, facilities, suppliers, stock, vehicles, telemetry, source_health |

## Contract columns
Every column in `100_brain_tables.sql` and `200_senses_tables.sql` as of Gate 0 is a contract
column: it may not be dropped, renamed or retyped. Owners may add nullable columns or columns
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
