# contracts/ — FROZEN after Gate 0

Shared agreement between Instance A (Brain) and Instance B (Senses & Face).
Nobody edits these files after Gate 0. To request a change, write
`contracts/requests/<A|B>-<topic>.md` (what, why, who is affected, migration plan) and stop;
the human decides and applies it.

| File | What it fixes |
|---|---|
| `ownership.txt` | Which instance may touch which path |
| `db/schema.sql` | The whole baseline: extensions, enum types, all 14 tables, read views (`v_*`) |
| `db/patches/` | Approved contract changes applied after the baseline (001: places + `v_places`) |
| `db/SCHEMA.md` | Table ownership, column meaning, evolution rules |
| `events.md` | Redis Streams catalog |
| `events.schema.json` | JSON Schema generated from the Python models (do not hand-edit) |
| `openapi.yaml` | REST contract for both sides |
| `python/reliefroute_contracts/` | Pydantic enums + event models both sides import |
| `tools/check_ownership.py` | Gate self-audit (b): changed files belong to your role |
| `tools/audit_runtime.py` | Gate self-audit (a): no mocks/fake data/hardcoded coords in runtime code |

## Rules that bind both sides
1. No cross-writes. A writes plans, routes, orders, order_lines, approvals, notifications,
   agent_events, LangGraph checkpoint tables. B writes hazards, facilities, suppliers, stock,
   vehicles, telemetry, source_health.
2. Cross-reads use the `v_*` views (`v_active_hazards`, `v_vehicle_status`,
   `v_vehicle_assignment`, `v_available_stock`, `v_source_freshness`, `v_facilities`,
   `v_suppliers`, `v_places`). Reading your own
   tables directly is fine.
3. Owners may add columns, indexes and private tables in their own `1xx`/`2xx` migrations.
   They may not drop, rename or retype a column listed in `db/SCHEMA.md`.
4. Python code imports enums/events from `reliefroute_contracts`; never redefine them.
5. `backend/app/main.py` auto-registers `app.api_brain.router:router` and
   `app.api_senses.router:router`. Each side only edits its own package.
6. `backend/app/core/` is owned by A but its public API (`config.CommonSettings`,
   `db.get_pool`, `bus.publish` / `bus.consume`, `resilience.CircuitBreaker` /
   `resilience.resilient_get`, `logging.configure_logging`) is stable from Gate 0. A will not
   make breaking changes to these without a contract request.
