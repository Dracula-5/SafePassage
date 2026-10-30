# A-G0 — Contracts and skeleton (Instance A, Brain)

Branch `feat/brain`, tag `phase0-contracts`, both pushed to https://github.com/Dracula-5/SafePassage.

## What was built
**Contracts (`contracts/`, frozen from this tag)**
- `db/schema.sql`: the whole database baseline. It contains PostGIS and pgcrypto, 17 enum types, and all 14 tables:
  - B's 7 tables: hazards, facilities, suppliers, stock, vehicles, telemetry, source_health.
  - A's 7 tables: plans, routes, orders, order_lines, approvals, notifications, agent_events.

  It also has GIST indexes on every geometry column, and 5 read views: `v_vehicle_assignment`, `v_active_hazards`, `v_vehicle_status`, `v_available_stock` (stock minus open orders) and `v_source_freshness`. `db/SCHEMA.md` explains the semantics and the evolution rules.
- `events.md`: the 6 Redis Streams with envelope, payloads, consumer groups and dedupe keys. The source of truth is `python/reliefroute_contracts/` (Pydantic enums plus a discriminated `EventEnvelope`), and `events.schema.json` is generated from it.
- `openapi.yaml`: OpenAPI 3.1 covering every B path (`senses` tag) and every A path (`brain` tag), plus `/health`.
- `ownership.txt`, `tools/check_ownership.py` (gate audit b), `tools/audit_runtime.py` (gate audit a).
- `docs/MASTER_PROMPT.md`: the master prompt as you gave it, saved because the instance prompts tell both instances to read it.

**Migrations**
- `app.core.migrate` applies `contracts/db/schema.sql` once, then `backend/migrations/1xx_*` (A) and `2xx_*` (B) in name order. Migrations are checksummed; editing an applied file is an error, and other prefixes are rejected. There are no owner migrations yet.

**Backend skeleton (A)**
- `app/main.py`:
  - Routers are discovered automatically from `app.api_brain.router` and `app.api_senses.router`.
  - A missing package is tolerated, so either side runs alone. A package that fails to import crashes startup loudly.
  - `GET /health` checks Postgres and Redis and returns 503 when either is down. Connections are lazy, so the app boots without them.
- `app/core/`:
  - `config` (pydantic-settings) and `logging` (structlog JSON).
  - `db`: an asyncpg pool.
  - `bus`: validated publish; ack after the handler finishes; re-claim pending entries after 60 s; invalid entries go to a dead-letter stream.
  - `resilience`: timeout, jittered retry and circuit breaker.
  - `migrate`.
- `app/api_brain/router.py`: every brain path is registered, and each returns **501** until its gate lands. No endpoint returns invented data.

**Removed so I stay in my area:** the first Gate 0 draft included `docker-compose.yml` and a `200_senses_tables.sql`, both B's files. Both are deleted; B's tables now live in the contract. A suggested compose setup for B is below.

## Commands run (real output)
```
$ cd backend && .venv/bin/python -m pytest -q -rs
SKIPPED [1] tests/brain/test_integration_bus.py:30: TEST_REDIS_URL not set
SKIPPED [1] tests/brain/test_integration_db.py:50: TEST_DATABASE_URL not set
SKIPPED [1] tests/brain/test_integration_db.py:60: TEST_DATABASE_URL not set
SKIPPED [1] tests/brain/test_integration_db.py:85: TEST_DATABASE_URL not set
37 passed, 4 skipped, 1 warning in 1.06s

$ pglast.parse_sql(contracts/db/schema.sql)     # real Postgres parser, syntax only
48 statements parsed OK; Counter({'CreateEnumStmt': 17, 'CreateStmt': 14, 'IndexStmt': 10,
                                  'ViewStmt': 5, 'CreateExtensionStmt': 2})

$ python contracts/tools/audit_runtime.py
0 finding(s)        # a planted "(85.8245, 20.2961)  # mock" probe produced 2 findings, then was removed

$ uvicorn app.main:app   (DB/Redis URLs pointed at closed ports)
GET  /health            -> 503 {"status":"degraded","checks":{"postgres":"error","redis":"error"}}
POST /api/plans/trigger -> 501 {"title":"Not Implemented","status":501,...}

secret scan: no matches; backend/.venv and backend/.env are git-ignored
```
The tests cover:
- Python, SQL and OpenAPI enums are identical.
- `events.schema.json` is current.
- The OpenAPI spec is valid.
- Every brain path in the contract is served.
- Router discovery fails the right way.
- The breaker and retry behave as designed.
- Ownership globs resolve correctly.

## Self-audit
- (a) No mock words, coordinate pairs or GeoJSON literals in runtime code.
- (b) Files written outside A's list are Gate 0 shared files only: `contracts/*`, `.gitignore`, `docs/MASTER_PROMPT.md`, `backend/requirements/{base,dev}.txt`, `backend/pytest.ini`, `backend/tests/__init__.py`. Nothing under B's paths.
- (c) No secrets; `.env` is ignored.
- (d) `resilience.resilient_request` gives every outbound call a timeout, retry and breaker. Redis and Postgres calls have timeouts.
- (e) There are no agent outputs yet. Every event is validated by Pydantic both when published and when consumed.

## Update after Docker became available (integration tests now run)
```
$ docker run postgis/postgis:16-3.4 (port 55432, amd64 image under emulation) ; docker run redis:7-alpine (port 6380)
$ TEST_DATABASE_URL=postgresql://reliefroute:reliefroute@localhost:55432/postgres \
  TEST_REDIS_URL=redis://localhost:6380/15 .venv/bin/python -m pytest -q -rs
41 passed, 1 warning in 5.12s
```
`schema.sql` applies on real PostGIS, and re-running it is a no-op. All 5 views exist. `v_available_stock` subtracts open orders (1000 - 300 = 700; the cancelled order is ignored), and the drill CHECK rejects bad rows. The Redis bus round trip works, including dead-lettering of an invalid entry.

## Known gaps
- Host port 5432 is taken by a local Homebrew Postgres 14 and 5433 is also in use. B's compose should map PostGIS to a free host port (A's local `.env` uses 55432).
- The remote has no `main` branch yet (A must not push to main).

## Decisions taken
- **Demo region: Andhra Pradesh.** Live check on 2026-10-30: 46 of the last 100 SACHET alerts (about 17 h) came from Andhra Pradesh SDMA, and 3 more from IMD Visakhapatnam. That is far more than any other state. October to December is the Bay of Bengal cyclone and north-east monsoon season, so real alerts should keep arriving during the demo. Valhalla will use Geofabrik `asia/india/southern-zone`, clipped to Andhra Pradesh (Docker has about 3.8 GB RAM; the full zone may not build).
- **LLM provider: Gemini.** The key goes in `backend/.env` (git-ignored). `env/brain.env.example` has the slots.

## For B (suggested; B owns `docker-compose.yml`)
Services A needs locally:
- `postgis/postgis:16-3.4` with user, password and db all `reliefroute`, on port 5432.
- `redis:7-alpine` on port 6379.
- `ghcr.io/valhalla/valhalla-scripted` on port 8002, with `tile_urls` set to the demo region's Geofabrik `.osm.pbf`.

Set up the Python environment with:
`uv venv backend/.venv --python 3.12 && uv pip install -e contracts/python -r backend/requirements/senses.txt -r backend/requirements/dev.txt`
(`senses.txt` should start with `-r base.txt`.)

## Questions for the human
1. **Git remote:** please add one, then I'll push `feat/brain` and the tag. B also needs the contracts, either by merging `feat/brain` up to `phase0-contracts` into `main` or by branching from the tag.
2. **Demo region:** it sets the Valhalla extract. Building the tiles for an Indian zone takes hours, so this blocks the real-Valhalla test in Gate 1.
3. **LLM provider and API key** for Gate 2.
4. **Docker or OrbStack:** needed for PostGIS, Redis and Valhalla before Gate 1 can be verified.
