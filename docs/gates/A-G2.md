# A-G2: Agent graph end to end (Instance A, Brain)

Branch `feat/brain` (merged with `main`, including contract patch 001). Integrated with B in a
**local-only** worktree `integration/gate2` (`~/Desktop/SafePassage-int`, never pushed) so one app
serves both routers.

## What was built
| Area | Module | Notes |
|---|---|---|
| LLM | `app/llm/client.py`, `factory.py` | Provider-agnostic `generate(system, prompt, schema)`. The JSON reply is validated with Pydantic; on failure the exact validation error goes back to the model (repair-and-retry, 3 attempts). Gemini is called over REST through `core.resilience`. The model comes from `LLM_MODEL`, or the newest stable "flash" model the key can list. |
| Fallbacks | `app/graph/fallbacks.py` | With no key, an outage, or invalid output after repair, rule-based triage and brief are used. Each one starts with "RULE-BASED FALLBACK" and is logged as `data_stale`/`error`; nothing is presented as model reasoning. |
| Standards | `app/agents/standards.py` | Water: 15 L/person/day (Sphere 2018, Water supply standard 2.1). Food: 2,100 kcal/person/day (Sphere, Food security standard 6.1) as a raw-rice energy equivalent at 365 kcal/100 g (USDA FoodData Central). Sources are cited in code. |
| Sentinel | `nodes.sentinel` + `reasoning.triage` | Facts from `v_active_hazards`, `v_places` and `v_facilities` go to the LLM, which returns a bounded `Triage` (priority, 12–72 h horizon, rationale, risks). |
| Needs | `needs.compute_demand` | Census population × standards × horizon, split over safe relief points (shelters, schools and hospitals outside **every** active hazard, within 15 km). No population means no demand, not a guess. |
| Procurement | `procurement.allocate` | Live `v_available_stock` + `v_suppliers`, ranked by hazard-aware Valhalla travel time, split across suppliers. Shortfalls carry an exact reason ("no supplier holds this item" / "cannot reach safely" / "insufficient stock"). |
| Route Planner | `planner.plan_routes` | Valhalla matrix with `exclude_polygons`, then the OR-Tools **pickup/delivery** VRP (new: pairs, shared stop nodes, peak-load check), then per-truck Valhalla geometry. Orders too heavy for one truck are split. The exclusion margin grows 250 → 500 → 1000 m on each retry. |
| Critic | `critic.review` | Re-reads the **current** hazards and checks Shapely clearance (≥100 m), peak load ≤ capacity, ETAs within the horizon, and pickup before drop-off. It rejects with exact violations; the planner retries, up to 3 times. |
| Supervisor / approval | `nodes.brief`, `nodes.approval_gate` | The LLM writes a brief for the approver. Approval is required for: Sentinel recommendation, high/critical priority, severe/extreme hazard, more than 5 t, unverified supplier, drill, or shortfalls. The gate node calls `interrupt()` with no side effects, because LangGraph re-runs it on resume. |
| Dispatcher | `graph/dispatcher.py`, `dispatch/` | One transaction writes routes, orders (idempotency key = hash of plan, supplier, destination, lines) and lines. Then: driver instructions translated to Telugu/Hindi (a schema validator rejects any translation that changes a number), PO PDFs, and SMS/WhatsApp records with idempotency keys. **Twilio isn't configured, so messages are recorded as "not sent".** |
| Graph | `graph/build.py` | LangGraph `StateGraph` with `AsyncPostgresSaver` (Postgres checkpointer). `PlanRunner.start/resume`. |
| Worker | `graph/worker.py` | Consumes `plan.requested` (claims pending → planning atomically), `plan.approved` (resumes), and `hazard.updated` (auto-plan for drills and severe/extreme flood-type hazards, one plan per hazard). Each plan is crash-guarded. |
| API | `api_brain/router.py` | Trigger (Idempotency-Key), list, detail, approve (first decision wins, then 409), orders, vehicle assignment, brain health. `main.py` now returns `application/problem+json` (the B-G1 gap). |
| Audit | `graph/audit.py` | Every tool call, result, decision, rejection, approval and dispatch goes to `agent_events` with source IDs. Each write autocommits, so the SSE timeline is live. |

## Evidence

### Tests (merged A+B code, live PostGIS, Redis and Valhalla)
```
$ cd ~/Desktop/SafePassage-int/backend && TEST_DATABASE_URL=... TEST_REDIS_URL=... VALHALLA_URL=... pytest -q -rs
SKIPPED [3] tests/brain/live_sources.py:37: SACHET polygon endpoint is rate-limiting this IP (HTTP 403)
155 passed, 3 skipped in 96.36s
```
**`test_integration_graph.py`** runs the whole graph on real PostGIS, the real Valhalla Andhra Pradesh graph and the Postgres checkpointer. The LLM is a scripted test double whose first Sentinel reply is malformed on purpose. It checks:
- the repair loop fixes the reply on attempt 2;
- the graph pauses at `awaiting_approval` with **0 orders** written;
- after approval and resume it reaches `dispatched` with orders, routes (min clearance ≥100 m), Telugu instructions, PO PDFs, and notifications marked "not sent";
- a second resume returns None, and re-running the dispatcher creates **no new rows** (idempotent);
- an offline driver leads to `failed`: "no online vehicle…".

Unit tests cover demand maths, allocation splitting and shortfall reasons, every Critic violation, the repair loop, the translation number guard, model selection and the labelled fallbacks. API tests cover the idempotent trigger with one `plan.requested`, 409 on early and second decisions, Problem bodies and 404s.

### Live run on real data
Hazard: a drill published through B's `POST /api/drill` over Vijayawada. The worker auto-planned it from `hazard.updated`.

**Run 1** (auto-triggered): `unreachable`. There were **0 safe relief points**, because my Gate 1 UI-test drill (about 80.3–81.15°E) was still active over the whole area, and the planner refuses to deliver into any active hazard. I ended that stale drill via `DELETE /api/drill/{id}`.

**Run 2** (manual `POST /api/plans/trigger`, Idempotency-Key `gate2-live-1`), full `agent_events` timeline:
```
17:14:14 [sentinel/node_started] Triage of 1 hazard(s)
17:14:14 [sentinel/tool_result] Read 1 active hazard(s) from v_active_hazards {hazard:9e1f44f6-d79b-47ea-ba59-6cb5b19c25bb}
17:14:15 [sentinel/tool_result] Population lower bound 1,048,240 from 1/104 settlements with census figures; 5 safe relief points within 15 km {view:v_places,view:v_facilities}
17:14:15 [sentinel/data_stale] LLM not configured; rule-based triage
17:14:15 [sentinel/decision] Priority high, plan 24 h: RULE-BASED FALLBACK (LLM not configured): priority from the worst severity (severe); default 24 h horizon. Needs human review. {hazard:9e1f44f6-…}
17:14:16 [needs/decision] 10 demand lines for 5 relief points over 1 day(s); population is a lower bound (1% settlement coverage) {facility:600c2ae9-…,facility:c3b3703a-…}
17:14:16 [procurement/tool_result] 0 stock rows with availability from v_available_stock
17:14:16 [procurement/decision] 0 orders across 0 supplier(s); 10 shortfall(s) escalated
17:14:16 [supervisor/node_finished] Plan failed: no supplier has reachable stock for this demand: escalated to humans
```
This is the correct outcome for today's real data. **No supplier has checked in any stock**, so Procurement escalates instead of inventing supply. The dashboard shows this plan live: the SSE indicator reads "● live", and the timeline, triage and the "Escalated to humans" box are visible (screenshot `b-g2-ops.png` in the session scratchpad).

The run exposed one bug, now fixed: the 1,048,240 figure is Vijayawada city's single census figure, counted in full because the city's centre lies in the drill zone, so "lower bound" was the wrong label. The message now states exactly what the number is, and the schema docstring explains the over/undercount.

## Self-audit
- (a) `audit_runtime.py`: 0 findings.
- (b) `check_ownership.py --role A --base origin/main`: OK.
- (c) Secret scan is clean. The key slots are in git-ignored `.env` files.
- (d) Valhalla, Gemini and Twilio calls all go through `resilient_request` (timeout, retry, breaker) with typed failures and fallbacks.
- (e) Every agent output is validated by Pydantic (`Triage`, `PlanBrief`, number-checked `Translation`, `Demand`, `Allocation`, `Routing`, `CriticVerdict`). The graph state holds only validated dumps.

## Not yet done or verified, and why
1. **No live LLM run.** `GEMINI_API_KEY` is still empty in `backend/.env`. Model selection, the Gemini request format and the translations have only been tested with test doubles.
2. **No live approval → dispatch on real data.** That needs real supplier stock (a portal check-in) and a real vehicle (the Driver PWA on a phone, which needs HTTPS via a tunnel; see the questions). The full path is proven with test fixtures on real PostGIS and Valhalla.
3. **Langfuse tracing isn't wired.** There are no Langfuse keys. `agent_events` is the audit trail; Langfuse is planned for Gate 4.
4. **Twilio isn't configured** (your choice), so every message is recorded as "not sent".
5. **Population data quality:** OSM census tags cover about 2.7% of settlements, and a city figure counts in full or not at all. WorldPop would fix this; that would need another contract change.
6. Auto-planning ignores lightning and storm alerts (SACHET's most common type) on purpose: they aren't relief-logistics events. Replanning on hazard growth is Gate 3.

## Questions
1. Paste the Gemini key into `backend/.env` and **save** (I'll copy it into the integration `.env`).
2. To finish the live E2E, can a team member check in stock **they actually hold, or a real supplier's figures**, at `/portal`? I won't enter stock myself.
3. Can I expose the local API through a temporary Cloudflare quick tunnel (a public random HTTPS URL, no account) so a phone can run the Driver PWA? Anyone with the URL could reach the API until it's stopped.
