# Gate 0 — Contracts and skeleton

Branch: `chore/gate0-contracts` (based on `main` @ `0501a28`). Author: the session that becomes Instance A after Gate 0.

## What was built
**Contracts (`contracts/`, frozen from now on)**
- `python/reliefroute_contracts/`: Pydantic enums, the 6 event payloads, a discriminated `EventEnvelope`, stream/group names, `VEHICLE_OFFLINE_AFTER_SECONDS = 120`. Both sides import this package; nobody redefines these types.
- `db/000_contract_types.sql`: PostGIS, pgcrypto, 17 enum types.
- `db/900_contract_views.sql`: cross-owner FKs and the read views `v_active_hazards`, `v_vehicle_status`, `v_vehicle_assignment`, `v_available_stock`, `v_source_freshness`.
- `db/SCHEMA.md`, `events.md`, `events.schema.json` (generated), `openapi.yaml` (OpenAPI 3.1, tagged `senses`/`brain`/`core`).
- `ownership.txt` plus `tools/check_ownership.py` (gate audit b) and `tools/audit_runtime.py` (gate audit a).

**Baseline migrations**
- `backend/migrations/100_brain_tables.sql` (A): plans, routes, orders, order_lines, approvals, notifications, agent_events.
- `backend/migrations/200_senses_tables.sql` (B): hazards, facilities, suppliers, stock, vehicles, telemetry, source_health. Written here as the contract baseline; **B owns it from now on**.
- The A→B foreign keys are in contract file 900, so 1xx never depends on 2xx.

**Backend skeleton (A-owned; its public API is stable for B)**
- `app/main.py`: auto-registers `app.api_brain.router` and `app.api_senses.router`. A missing package is skipped with a warning. A package that fails to import crashes startup. `/healthz` reports Postgres/Redis and returns 503 when either is down. Connections are lazy, so the app boots without them.
- `app/core/`: `config.CommonSettings`, `db.get_pool`, `bus.publish/consume` (validated envelopes, ack after the handler, re-claim after 60 s, dead-letter stream for invalid entries), `resilience.CircuitBreaker/resilient_get` (timeout, jittered retry, breaker), `migrate` (checksummed, ordered 000→1xx→2xx→900).
- `app/api_brain/router.py`: every brain path from the contract is registered. Each one returns **501 Not Implemented** at Gate 0, so the API serves no invented data.
- `docker-compose.yml` skeleton: PostGIS 16, Redis 7, and Valhalla behind the `routing` profile. **B owns it from now on.**

## Commands run (real output)
```
$ cd backend && .venv/bin/python -m pytest -v -rs
36 passed, 4 skipped, 1 warning in 0.91s
SKIPPED tests/brain/test_integration_bus.py:30: TEST_REDIS_URL not set
SKIPPED tests/brain/test_integration_db.py:42: TEST_DATABASE_URL not set
SKIPPED tests/brain/test_integration_db.py:52: TEST_DATABASE_URL not set
SKIPPED tests/brain/test_integration_db.py:77: TEST_DATABASE_URL not set

$ python contracts/tools/audit_runtime.py
0 finding(s)
# Sanity check: a probe file containing "(85.8245, 20.2961)  # mock" produced 2 findings and was then deleted.

$ uvicorn app.main:app  (DATABASE_URL/REDIS_URL pointed at closed ports)
GET  /healthz            -> 503 {"status":"degraded","checks":{"postgres":"error","redis":"error"}}
POST /api/plans/trigger  -> 501 {"title":"Not Implemented","status":501,...}

secret scan (sk-*, Twilio AC*, key/token/secret literals): no matches
git check-ignore: backend/.venv and backend/.env are ignored
```
The tests check that the Python enums, the SQL enums and the OpenAPI enums are identical, that `events.schema.json` is current, that the OpenAPI spec is valid, that every brain path in the contract is served, how router registration fails, and how the breaker and retry behave.

## Known gaps (not verified)
- **Migrations have never run against PostGIS, and the bus has never run against Redis.** Docker is not installed and the local Postgres 14 has no PostGIS. The 4 integration tests are written and skip until `TEST_DATABASE_URL` and `TEST_REDIS_URL` are set. Treat the SQL as unverified until they pass.
- `docker-compose.yml` has not been started. `postgis/postgis` may need amd64 emulation on Apple Silicon (OrbStack and Docker Desktop handle this).
- Live sources were only checked for reachability: the SACHET RSS returned HTTP 200. Ingestion is Gate 1.
- No README or architecture diagram yet. That is B's work at Gate 4.
- No remote exists, so nothing was pushed.

## Risks
- The Valhalla tile build for an Indian region takes a long time and a lot of RAM. Start it as early as possible once the region is chosen.
- `bus.consume` re-claims a handler failure indefinitely. A will add a delivery-count cap at Gate 1.

## Questions for the human
1. **Demo region?** It sets `VALHALLA_PBF_URL` (e.g. Geofabrik `asia/india/eastern-zone` for Odisha/WB cyclones, or the `southern-zone`), the Overpass bbox and the FCI godown filter.
2. **LLM provider and key** for the agents (Gate 1/2)?
3. Please create a GitHub remote. To start both instances: merge this branch into `main`, then branch `feat/brain` and `feat/senses-face` from it.

## To run the integration tests once Docker is installed
```
docker compose up -d db redis
cd backend
TEST_DATABASE_URL=postgresql://reliefroute:reliefroute@localhost:5432/postgres \
TEST_REDIS_URL=redis://localhost:6379/15 .venv/bin/python -m pytest -v -rs
```
