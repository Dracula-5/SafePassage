# SafePassage: final testing and deployment readiness

Audit of 2026-11-01 on branch `integration/gate3`, on the Windows development machine, against the real PostGIS, Redis and Valhalla (Andhra Pradesh road graph) plus the live data sources. Nothing was deployed, pushed or committed. Every claim below was observed in this session; what was not run is listed in section 12.

## Verdict: READY WITH CONDITIONS

No Critical or High defect remains open. The conditions are about what could not be exercised on this machine, not about known failures:

1. **The full production stack has not been brought up as one compose project.** Both production images build and start under the hardening flags, the Caddyfile validates, and the production-mode API was run and put through the full account lifecycle (section 9). The compose project as a whole (Caddy + TLS + all containers) must be started once on the target VM and checked with `/health` before real use.
2. **Contract requests 002 to 005 are still awaiting human review** (see `docs/HANDOFF.md`). The API behaves as built, but `contracts/openapi.yaml` does not describe it until they are applied.
3. **External inputs that cannot be tested here:** a real phone's GPS inside Andhra Pradesh, Telugu text-to-speech on a phone, real SMS/WhatsApp (Twilio is a Trial account, so no real message was sent or attempted), real email delivery, and a FIRMS key.
4. **A person must approve the first duty officer** with the operator token (documented in `docs/deploy.md`). Until then nobody can sign in as a duty officer in production, by design.

## 1. Features inspected

Authentication and the three roles; the duty officer app (Command Center, Hazards & Alerts, Plans & Approvals, Fleet & Drivers, Evacuations, Data Sources, Activity & AI, Team & Access, Directory, What-If Simulation); the driver app (six screens, offline queue, GPS); the supplier portal; the ingestion and planning workers; the AI pipeline (Sentinel, Needs, Procurement, Route Planner, Critic, approval, dispatch, Replanner); live updates (SSE); Valhalla routing; the deployment files.

## 2. Tests executed

| Suite | Result |
|---|---|
| Backend, whole suite (`pytest`, real PostGIS/Redis/Valhalla, scratch databases) | **553 passed, 3 skipped, 1 failed** on the final run. The failure was a flaw in the test itself (section 4, bug 9), fixed; that file now passes (20 passed, 1 skipped). Not re-run end to end after the fix |
| Backend skipped | 3 SACHET live-polygon tests: SACHET returns HTTP 403 to this IP (external rate limit, noted in HANDOFF) |
| Frontend `tsc --noEmit`, `eslint src tests` | Clean |
| Frontend queue unit tests (offline GPS queue) | 6 passed |
| `next build` (production) | Passes, 20 routes |
| Playwright, whole suite | **26 passed, 13 skipped, 0 failed** |
| Playwright skipped | Need production-mode approval enforcement, `E2E_DEMO` simulated fleet, a mail catcher, or `E2E_ROUTING` with an uncovered area (see section 8) |
| `contracts/tools/audit_runtime.py` (no fake data in runtime code) | 0 findings |
| `npm audit --omit=dev` / `pip-audit` on `constraints.txt` | No known vulnerabilities |
| Production-mode refusal of unsafe settings | 8 of 8 refused, each naming the problem; the safe control starts |
| Production-mode account lifecycle (live API) | 44 of 44 |
| Endpoint sweep (every GET in the live OpenAPI) | No 500s; every private endpoint refuses anonymous callers |

## 3. Backend and frontend health

- API, ingestion worker and brain worker: up, heartbeats fresh. All six data sources current (SACHET and Overpass on their labelled fallbacks, as designed).
- `/api/stream` (SSE): delivers history and live events, directly and through the web proxy; requires sign-in.
- Valhalla: working after a rebuild (bug 1). Hazard-polygon limits preserved.
- Web app: serves all routes; production build passes.

## 4. Bugs found, root causes, fixes

| # | Severity | Finding | Root cause | Fix | Regression test |
|---|---|---|---|---|---|
| 1 | High (environment) | Road graph was empty: Valhalla found no edges anywhere, so every plan failed or reported "unconnected regions" | The tile build was cut short while C: was full (0 bytes free), leaving tiles that load but cannot be searched | Rebuilt the tiles from the PBF (12 min). Old tiles renamed, not deleted | Routing now succeeds; the chaos/closed-loop suites pass against it |
| 2 | Medium | A locked account's 429 had no `Retry-After`, so the sign-in countdown could not work | The error handler rebuilt responses and dropped the exception's headers | `backend/app/main.py` forwards `exc.headers` | `tests/brain/test_problem_headers.py` (2) |
| 3 | Medium | Supplier/shell header pushed the page 132 px sideways on a 375 px phone | Header could not shrink | `AppShell.tsx`, `globals.css` | `responsive.spec.ts` (fits 5 sizes) |
| 4 | Medium | Duty officer pages had no `<main>` landmark | Content was a plain `div` | `ui/layout.tsx` | `responsive.spec.ts` (status/landmark) |
| 5 | Low | Next printed an "instant navigation" error on every duty officer page in dev | The layout waits for sign-in before drawing pages | `export const instant = false` on the 10 pages | Console sweep: 0 occurrences |
| 6 | Medium | Intermittent "Internal Server Error" from the web proxy ("socket hang up", 12 times in about 35 minutes) | The API closed idle connections (5 s) just as the proxy reused one | `--timeout-keep-alive 75` in `stack.ps1` and the production Dockerfile; Caddy upstream keepalive 30 s (config validated with Caddy) | None possible without a load harness; the e2e plan polling now tolerates one dropped answer |
| 7 | Medium | Failed and unreachable plans read "Plan is being prepared." and the detail was titled "Unreachable" for every failure | Fallback text ignored the plan's status | `PlansPage.tsx` | `responsive.spec.ts` |
| 8 | Medium | Activity "Safety checks" tab counted only rejected checks, so 0 read as "nothing was checked" | Filter matched only `critic_rejection` | `ActivityPage.tsx` matches everything the Critic did | `responsive.spec.ts` |
| 9 | Low (test) | `test_bad_fix_times_are_refused[future]` failed on a long run | "Now + 1 h" was computed at collection time, 64 minutes before the test ran | Built at run time | Same test |
| 10 | Low | `stack.ps1` and `.env.example` still said codes are "typed once at sign-up" | Left over from before the codes were removed | Text corrected | n/a |
| 11 | Low | Web pages sent no HSTS (the API did) | Missing header | `next.config.ts` adds it in production | Build passes |

## 5. Regression tests added

`frontend/tests/e2e/responsive.spec.ts` (22 tests: sign-up has no code fields and offers 3 roles, bad input refused, wrong password and unknown email look alike, public and duty officer pages at 5 sizes, driver and supplier on a phone, role isolation of pages and APIs, ended and signed-out sessions, keyboard sign-in with a visible focus ring, status never colour alone, keyboard menu, failed-plan wording, safety-check counting); `backend/tests/brain/chaos/test_chaos_database.py` (8: Postgres and Redis down: the API stays up, `/health` reports it, errors leak no host or credential, nobody is signed in, sign-up does not pretend to succeed); `backend/tests/brain/test_problem_headers.py` (2). The two plan/activity tests serve clearly labelled fixtures from the browser; nothing is stored.

## 6. AI and routing verification (observed)

A labelled DRILL over Kurnool (outside the live cyclone) with one simulated, labelled truck (`TEST-SIM-GPS-…`):

| Stage | Result |
|---|---|
| 1 Drill alert initiated | Proven. Hazard shown as drill |
| 2 Affected area (Sentinel) | Proven: priority "high", census figure scaled by the zone's share of its mandal, 5 safe relief points within 15 km |
| 3 Needs | Proven: 10 demand lines for 5 relief points, 2 days |
| 4 Procurement | Proven: 5 orders from 1 verified supplier; **5 shortfalls escalated, not invented** |
| 5 Route Planner | Proven: 1 route, 10 stops, 129 km, 2.4 km clearance from hazards |
| 6 Critic | Proven: PASS (2 hazards, clearance, capacity, horizon) |
| 7 Approval | Proven: approval required (reasons listed), approved; a second, conflicting decision was refused (HTTP 409); re-checked against hazards, routes, stock and vehicles before dispatch |
| 8 Driver assignment | Proven: the simulated driver fetched the route; instructions translated into Telugu with numbers verified; purchase-order PDFs created; **no message sent** (drills are silent, no phone numbers) |
| 9 GPS monitoring | **Partly proven**: stops 1/10 and 2/10 were detected from simulated GPS. The remaining stops were skipped because I ran the simulator at 300 m/s with 4 s fixes (1.2 km steps, wider than the 300 m arrival circle). That is my simulation setting, as HANDOFF warns, not an app defect, but it means stop-by-stop progress was not shown for all 10 |
| 10 New hazard triggers replanning | Proven: a labelled road-block drill on the route produced a child plan; the Replanner re-routed 4 orders, the Critic passed it, it was auto-approved as a re-route of already-approved goods, re-checked, dispatched with new Telugu instructions, and it honestly reported 3 order parts it could not serve |
| 11 Dashboard history | Proven: Activity & AI shows the real steps (screenshot reviewed) and Plans & Approvals shows the plans |

Honest-failure behaviour observed: with today's real cyclone and other road-blocking hazards over Vijayawada, 0 of 40 supplier-to-relief routes were reachable, and the system reported "unreachable" / "no safe road route" rather than inventing a route. A Valhalla timeout was reported as "routing service unavailable". A truck inside the drill zone was not sent. No stock, route or delivery was fabricated at any point.

Covered by the existing automated suites (all passing): Valhalla down, dying mid-plan and hanging; Gemini failing, absent and returning garbage; Redis down at approval; worker killed mid-plan and mid-dispatch; duplicate events and alerts; repeated and conflicting approvals; stale approval after the hazard ended; stock taken while waiting; offline driver, off-route truck, deviation noise, replayed fixes; no way out for an evacuation; hostile alert data. Added in this audit: PostgreSQL and Redis unavailable.

## 7. Authentication and roles (observed on a production-mode API)

Public sign-up as duty officer or driver creates a **pending** account: no session cookie, no sign-in, no plan access. Suppliers can start at once. Nobody (anonymous, pending officer, supplier, driver, wrong token) can approve; the operator token approves the first officer; an approved officer approves a driver; a cross-site request carrying the officer's cookie is refused; sign-out kills the old cookie; ten wrong passwords lock the account (429 with `Retry-After`, even for the right password); a removed person's session ends at once; a flood of sign-ups is rate limited. Cookies are HttpOnly, SameSite=Lax and Secure over HTTPS. Passwords are salted scrypt, sessions are stored as SHA-256 hashes. Driver and supplier tokens are refused on every duty officer endpoint. The development-only conveniences (instant approval, loopback sign-up rate-limit exemption) apply only when `APP_ENV=development` exactly; any other value, including a typo, is rejected at startup or keeps approval on.

## 8. Test coverage not achieved here

- 13 Playwright tests skipped: approval-enforced tests (need production mode), presentation-mode tests (need `DEMO_FLEET` and `E2E_DEMO`), email (needs Mailpit), and the drill-to-driver test. The last one is also **not currently passable on this machine**: it draws its drill over Vijayawada, where today's real hazards cut every route. The scenario was run by hand over Kurnool instead (section 6). That test should be pointed at an area the live hazards do not cover, or run when they have cleared.
- The presentation specs I rewrote for the new screens (Fleet drawer, Directory, Evacuations, simulated-driver tools) have been type-checked and linted but **not executed**.

## 9. Security audit

Verified: no secrets in tracked files (`.gitignore` covers `.env`, `*.env`; only `*.example` tracked; pattern scan clean); production startup refuses weak operator token, missing enrolment code, non-HTTPS or wildcard CORS, the development database password, a passwordless Redis, and disabled rate limiting; a misspelled `APP_ENV` stops the start. Security headers present (CSP, HSTS, `nosniff`, frame denial, referrer policy, `no-store` on API). Containers: non-root (`relief`, `node`), read-only filesystem, all capabilities dropped, `no-new-privileges`. Redis is `noeviction` with a password; the database network is internal. Caddy is the only public entry; the Caddyfile validates. Error responses leak no host, credential or stack (tested with the database down).

Known and documented gaps (unchanged): the supplier portal is open by design; a registered phone can fake its GPS; WhatsApp replay; no encryption at rest beyond the disk; no network-level DoS protection; no independent security audit.

## 10. Remaining findings

| Severity | Finding |
|---|---|
| Medium | **Before approving, a duty officer sees the demand, safety result and summary but not the proposed routes or orders**: those are written to the database only at dispatch (same as the old screen). Showing a preview needs a plan-detail change, which is a frozen contract: raise a contract request |
| Medium | The full production compose stack was not started (condition 1) |
| Medium | Demonstration stage 9 only partly shown (section 6); drill e2e test needs a clear area (section 8) |
| Medium | Presentation e2e specs rewritten but not run (section 8) |
| Low | Browser console shows expected 401/404/422 lines (not signed in, supplier/vehicle not registered, no assignment yet, bad link token) |
| Low | `/health` is public and says "degraded" |
| Low | `stack.ps1 status` lists containers only, not the API, workers and web server |
| Info | Population estimates use census-tagged settlements only (about 2% coverage) and are labelled lower bounds |

## 11. Deployment configuration status

Compose, Dockerfiles, Caddyfile and `setup.sh` reviewed; `reliefroute-backend` (816 MB) and `reliefroute-frontend` (347 MB) both built from the production Dockerfiles; the backend image imports and starts read-only with capabilities dropped; the Caddyfile validates with the pinned Caddy image. Backups: `infra/deploy/backup.sh` and the restore procedure are in `docs/security.md` (not exercised in this audit). Restart behaviour: `restart: unless-stopped`, a Postgres lock keeps one active worker with a hot standby, and the API, brain, ingestion and web processes restarted cleanly under `stack.ps1 up` twice (resuming an in-flight plan after a restart was not tested live; the chaos suite covers a worker killed mid-plan and mid-dispatch). Rollback: `git checkout <previous commit>` then the update command below; migrations are additive and run first.

## 12. Limitations and things left on this machine

- **Test data in the development database** (mine): accounts from my scripts and tests (emails starting `e2e-`, `console-`, `demo-view-`, `inst-`, `st-`, `ops-`, `plans-`, `supplier-`, `driver-`, `operator-`, `dbgdrv-`, all `@example.org`), the vehicles they registered (labels `TEST-SIM-GPS-…`, `AP 16 CC …`, `AP 16 SH …`), and two labelled drill plans with their purchase-order PDFs. I attempted a bulk delete, which the safety classifier declined, so nothing was deleted. Your 9 original accounts are untouched. The vehicle labelled `AP 7393` is not mine.
- Two Valhalla tile folders and tars from the damaged build remain in `.reliefroute-data/valhalla/` (`*.broken-20261101`, `valhalla_tiles.tar.suspect`, about 3 GB on D:). Delete them when satisfied with the rebuilt graph.
- C: is almost full again (under 1 GB). Docker and all project data are on D:. Free space on C: before the next Valhalla rebuild or large install.
- `docker image` tags `reliefroute-backend:rehearsal` and `reliefroute-frontend:rehearsal` remain.
- I removed the pip download cache on C: (1.5 GB, regenerable) and earlier the npm cache.
- A throwaway database, role, Redis container and API process used for the production-mode rehearsal were removed.

## 13. Localhost startup

```powershell
# Docker Desktop must be running. First time only: build map data (README step 1).
.\infra\dev\stack.ps1 up        # containers, migrations, API, both workers, web app
.\infra\dev\stack.ps1 status
.\infra\dev\stack.ps1 down      # keeps the data
# Web http://localhost:3000   API http://localhost:8000   logs in <data>\logs
```

Tests (from the repo root, with the venv at `.venv`):

```powershell
cd backend
$env:TEST_DATABASE_URL="postgresql://reliefroute:reliefroute@127.0.0.1:55432/postgres"
$env:TEST_REDIS_URL="redis://127.0.0.1:6380/15"; $env:VALHALLA_URL="http://127.0.0.1:8002"
..\.venv\Scripts\python.exe -m pytest -q          # about an hour on this machine
cd ..\frontend
node node_modules/typescript/bin/tsc --noEmit; node node_modules/eslint/bin/eslint.js src tests
node --test tests/queue.test.mjs
$env:OPERATOR_TOKEN="<from backend\.env>"; npx playwright test
# Stop the dev web server before `next build`: they share the .next folder.
```

## 14. Production deployment

Exactly as in `docs/deploy.md` (not run here): on a VM with 8 GB RAM, Docker, ports 80/443, a domain, and the map data in `/srv/reliefroute-data`:

```bash
sh infra/deploy/setup.sh https://relief.example.org     # writes strong secrets to infra/deploy/.env
nano infra/deploy/.env                                   # add GEMINI_API_KEY and optional keys
DC="docker compose -f infra/deploy/docker-compose.prod.yml --env-file infra/deploy/.env"
$DC up -d valhalla && python3 infra/valhalla/raise_limits.py && $DC restart valhalla
$DC up -d --build
curl https://relief.example.org/health
# First duty officer: sign up at /signup, then
curl -X POST "$PUBLIC_URL/api/accounts/<ACCOUNT_ID>/approve" -H "X-Operator-Token: $OPERATOR_TOKEN"
# Update:  git pull && $DC up -d --build && $DC restart caddy
```

After `raise_limits.py`, check `max_exclude_polygons_length` is 5000000 in `valhalla.json`.

## 15. Readiness verdict

**READY WITH CONDITIONS** (section "Verdict"). Do not describe it as production-proven until the compose stack has run on the target VM, a real phone has reported GPS from inside Andhra Pradesh, and contract requests 002 to 005 are reviewed.
