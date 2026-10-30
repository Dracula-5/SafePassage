# MASTER PROMPT: ReliefRoute (working name)

## Mission
Build "ReliefRoute": an autonomous emergency-response and supply-chain logistics swarm for a Gen-AI buildathon (Theme 2: Climate, Resource Infrastructure & Global Supply Resilience). It continuously monitors weather/hazard alerts and fleet telemetry, queries supplier inventory, recalculates safe transit routes around affected zones, and dispatches relief orders end to end, with a human approval gate for high-stakes actions.

## Hackathon rules that drive every decision
Scoring: Technical depth 20, AI reasoning and agentic execution 25, Relevance/impact/USP 25, Live demo and robustness 30.
- ZERO-TOLERANCE: no hardcoded JSON, static mock datasets, or fake placeholder numbers at runtime. Everything must come from live APIs, live databases, or official open datasets. Test fixtures may exist ONLY under tests/ and must never be imported by runtime code.
- The app must run live without crashing. No pre-recorded demos.
- Core logic, AI models, and agent workflows must be written during the hackathon.
- Every team member must be able to explain the code. Keep it modular, typed, commented where the logic is non-obvious.
- Public repo needs a clean README with setup steps and an architecture diagram.

## Design principles
1. LLMs reason, solvers compute. The LLM interprets alerts, plans, and explains. OR-Tools solves routing/allocation; Shapely verifies geometry. The LLM never invents coordinates, distances, or stock numbers.
2. Event-driven closed loop: a hazard change or a vehicle deviation triggers an automatic re-plan.
3. Plan, Verify, Act: a Critic node rejects any plan violating constraints and sends it back with the exact violation.
4. Every decision is auditable: each action records tool calls, inputs, source IDs, timestamps into agent_events.
5. Confidence-aware degradation: stale or missing data is flagged and a fallback source is used, never a guess.
6. Idempotency on every dispatch.

## Architecture
Live sources: NDMA SACHET (CAP alerts), IMD, Open-Meteo, GDACS, NASA FIRMS, CWC/India-WRIS, OSM (roads via self-hosted Valhalla, facilities via Overpass), Data.gov.in (e.g. FCI godowns), Driver PWA streaming real phone GPS, Supplier check-in portal (suppliers update real stock via web form/WhatsApp).

Ingestion (deterministic, no LLM): pollers with circuit breakers and TTL/staleness tags, normalized into PostGIS (hazard polygons + severity), events published on Redis Streams.

LangGraph supervisor with Postgres checkpointer, nodes:
1 Sentinel: triage alert into affected zone, severity, ETA.
2 Needs Agent: population in zone x Sphere-style standards into demand per item.
3 Procurement: query live stock, rank and split across suppliers, escalate if short.
4 Route Planner: hazard polygons into Valhalla exclude_polygons, plus OR-Tools VRP (capacity, time windows).
5 Critic: verifies geometry (no hazard intersection), capacity, time windows; rejects with a reason (retry loop, max 3).
6 Dispatcher: orders, idempotency keys, driver instructions (Hindi/regional language, voice), PDFs, WhatsApp/SMS via Twilio.
Replanner: consumes telemetry deviation / hazard growth, loops back to node 4.
Human approval gate before high-stakes dispatch.

UI: Next.js + MapLibre live map, hazard layers, vehicles, agent reasoning timeline over SSE, approval panel, what-if drill tool (judge-drawn hazard zone, labelled DRILL, inserted as a real hazard row with source=drill).

Edge cases that must work and be demoable: API timeout (breaker + fallback + "data stale" badge); no single supplier has enough stock (split or escalate); no safe route (flag unreachable, propose alternate hub); driver offline (last-known position, reassign load); malformed LLM output (Pydantic repair, retry); duplicate alerts / duplicate dispatch (dedupe + idempotency).

Stack: FastAPI, LangGraph, PostGIS, Redis Streams, OR-Tools, Valhalla, Shapely, Pydantic, Next.js + MapLibre, Langfuse, Twilio. Backend on Fly.io/Railway, frontend on Vercel.

## Repository layout and OWNERSHIP (strict; two Claude Code instances work in parallel)
Instance A ("Brain") owns:
  backend/app/main.py, backend/app/core/, backend/app/graph/, backend/app/agents/,
  backend/app/solvers/, backend/app/routing/, backend/app/dispatch/, backend/app/llm/,
  backend/app/api_brain/, backend/migrations/1xx_*.sql, backend/tests/brain/,
  backend/requirements/brain.txt, env/brain.env.example, docs/brain.md, docs/gates/A-*.md
Instance B ("Senses & Face") owns:
  backend/app/ingestion/, backend/app/telemetry/, backend/app/suppliers/, backend/app/api_senses/,
  backend/migrations/2xx_*.sql, backend/tests/senses/, backend/requirements/senses.txt,
  env/senses.env.example, frontend/, driver-pwa/, infra/, docker-compose.yml, README.md,
  docs/senses.md, docs/demo-script.md, docs/gates/B-*.md
Shared, FROZEN after Gate 0: contracts/ (db schema, event catalog, REST contract). Do not edit.
  If you need a change, write contracts/requests/<A|B>-<topic>.md and stop; the human decides.
main.py auto-registers routers from app.api_brain.router and app.api_senses.router, so neither side edits the other's code.
Never touch files outside your ownership list, even to "quickly fix" something. Report it instead.

## Data ownership
B owns tables: hazards, facilities, suppliers, stock, vehicles, telemetry.
A owns tables: plans, routes, orders, approvals, agent_events, checkpoints.
Cross-reads use views defined in contracts/ (e.g. v_vehicle_assignment). No cross-writes.

## Event contract (Redis Streams)
hazard.updated, vehicle.telemetry, supplier.stock.updated (emitted by B);
plan.requested, plan.approved, vehicle.deviation (emitted by A). Payload schemas live in contracts/events.md.

## REST contract
B serves: GET /api/hazards, /api/facilities, /api/vehicles; POST /api/telemetry; POST /api/drill; supplier portal under /portal/*; GET /api/stream (SSE tailing agent_events + hazards).
A serves: POST /api/plans/trigger; GET /api/plans/{id}; POST /api/plans/{id}/approve; GET /api/orders; GET /api/vehicles/{id}/assignment.
Full OpenAPI in contracts/openapi.yaml.

## Gates (STOP points)
Gate 0 Contracts and skeleton. Gate 1 Live data, live map, standalone routing. Gate 2 End-to-end graph on a real alert with approval gate. Gate 3 Closed-loop replanning with real phone GPS plus all edge cases. Gate 4 Polish, deploy, README, demo script.

At EVERY gate, before stopping, you must:
1. Run the full test suite for your area and the real feature end to end against live sources. Paste the actual output into your gate report; never claim something works without running it.
2. Self-audit checklist: (a) grep runtime code for hardcoded coordinates, sample JSON, "mock", "dummy", "fake", "lorem"; (b) `git status` shows only files in your ownership list; (c) no secrets committed, .env files ignored; (d) every external call has a timeout, retry, and failure path; (e) every agent output is Pydantic-validated.
3. Write docs/gates/<A|B>-G<n>.md: what was built, commands run with real output, known gaps, risks, questions.
4. Commit in small logical commits on your own branch (feat/brain or feat/senses-face), rebase on latest main, push the branch. Do NOT push to main.
5. STOP and wait for the human to say "continue". Do not start the next gate on your own.

## Working style
Be honest: if something is stubbed, blocked, or unverified, say so in the gate report. If a live source is unreachable or an API key is missing, stop and ask instead of substituting fake data. Prefer boring, tested code over cleverness. Keep functions small and typed. Log with structured logging.
