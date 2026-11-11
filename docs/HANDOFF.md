# ReliefRoute: handoff (status on 2026-10-31)

Start here when continuing the project. The rules are in `docs/MASTER_PROMPT.md`, setup is in `README.md`, and per-gate evidence is in `docs/gates/`.

## 1. Where things are

Updated 2026-11-11: at the owner's request, everything is on `main`.

| Branch | Contents |
|---|---|
| `main` | **Everything.** Work continues here. CI, Vercel and Render build from it |
| `feat/brain` | Fast-forwarded to `main` on 2026-11-11, so it matches. Still the GitHub default branch until the owner switches it to `main` (Settings > General > Default branch) |
| `integration/gate3` | Gates 3 and 4, up to 2026-11-03. Fully contained in `main` |
| `integration/gate2`, `feat/senses-face` | Gate 2 history. Fully contained in `main` |

Tags: `phase0-contracts`, `phase1-routing`, `phase1-senses`. Gate 2 and 3 are not tagged because they aren't fully verified (see §3).

One worker carried both roles through Gates 3 and 4, on one integration branch with commits scoped per owner. If two instances work in parallel again, go back to separate branches: `contracts/ownership.txt` still defines who owns what.

`contracts/` is frozen. **Two contract requests are pending human review:**

- **002** (`contracts/requests/A-plan-and-admin-views.md`): two read-only views, added as owner migrations.
- **003** (`contracts/requests/A-security-hardening.md`): the access rules, new endpoints and limits. `openapi.yaml` does not describe these until it is applied.
- **004** (`contracts/requests/B-accounts-and-one-web-app.md`): email accounts and the single web app.
- **005** (`contracts/requests/B-email-links.md`): email confirmation and forgot-password.

## 2. Gate status

| Gate | Status |
|---|---|
| 0 Contracts and skeleton | ✅ |
| 1 Live data, map, routing | ✅ |
| 2 Pipeline, approval, driver and supplier apps | 🟡 Proven live end to end, except the two items in §3 |
| 3 Closed loop and edge cases | 🟡 Built and proven live with labelled simulated vehicles |
| 4 Polish, docs, deploy | 🟡 Docs done. Security reviewed and hardened. Production stack rehearsed end to end on this machine. Not deployed |

Tests at the last run: **473 tests passing**, with SACHET's live polygons included, on real PostGIS, Redis and Valhalla. PWA queue: 6 passed. Self-audit: 0 findings. Evidence is in `docs/gates/A-G3.md`, `docs/gates/A-G4-security.md` and `docs/gates/B-G5-web-app.md`.

The local database holds live data only: this session's drills, plans and test vehicles were cleared. Their PO PDFs, labelled DRILL, remain in `~/reliefroute-data/documents/`.

## 3. What is left, and who it needs

| Item | Needs |
|---|---|
| Telugu voice on a phone | A real phone with a Telugu speech pack. The Telugu text itself is verified |
| Real-phone GPS | A teammate **inside Andhra Pradesh**, through a tunnel. The road graph covers AP only, and vehicles over 200 km from the relief points are ignored |
| Real water stock | A supplier check-in at `/supplier`. `infra/demo/seed_demo_water.py` was not run here; it stays labelled, and running it means disclosing it |
| Deploy | A VM with 8 GB RAM and a domain name pointing at it. Then follow `docs/deploy.md`. No Vercel account is needed: the dashboard is now in the production stack |
| SMS and WhatsApp | Twilio's key is valid, but it is a Trial account, which only reaches numbers verified with Twilio. No real message has gone out: drills send nothing by design |
| Review contract requests 002 to 005 |
| Merge into `main` | The human owner. Ideally a PR from `integration/gate3` |

| FIRMS fire data | A valid MAP_KEY. NASA rejected the value in `infra/deploy/local.env`: real keys are 32 characters |
| Email delivery | An email provider's SMTP details in `.env` (see `docs/deploy.md`). The feature is built and works against a real SMTP server; only delivery through a real provider is untested |

Once a real phone is verified, add the output to the gate reports and tag `phase2-*` and `phase3-*`.

## 4. Running on this machine (Windows)

The project is in `C:\Users\dheer\OneDrive\Desktop\safepassage`. It sits in a OneDrive folder, which syncs `node_modules` and `.git`; if that causes file-lock errors, move it out.

```powershell
.\infra\dev\stack.ps1 up        # containers, migrations, API, both workers, dashboard
.\infra\dev\stack.ps1 status
.\infra\dev\stack.ps1 down      # keeps the data
.\infra\dev\stack.ps1 prod      # the production images instead, everything at http://localhost:3000
.\infra\dev\stack.ps1 prod-down # stops those and keeps their data
```

`up` and `prod` each stop the other, because they share port 3000. They have separate databases and separate secrets: `backend\.env` for `up`, and `infra\deploy\local.env` for `prod`.

| Thing | Location |
|---|---|
| Operator token, enrolment code | `backend/.env`, created by the first `stack.ps1 up` |
| Python venv | `C:\Users\dheer\reliefroute-venv`, outside OneDrive because sync locks break installs |
| Map data, logs, PO PDFs | `C:\Users\dheer\reliefroute-data` |
| Compose settings | Root `.env`: `DB_HOST_PORT=15432`, because Windows has reserved 55432 |
| Backend settings | `backend/.env`, git-ignored |

Valhalla takes about 35 minutes to build on Windows, compared with 5 on macOS, because tile I/O goes through the file mount. It only happens once.

**Disk swings a lot.** Free space went from 2 GB to 29 GB across a reboot, because Windows clears its page and swap files. Docker's virtual disk alone is about 25 GB. Docker Desktop does not start at boot here, so run it before `stack.ps1 up`; it also auto-starts unrelated `zap-airflow` containers (`restart=always`), which share its 6 GB VM.

**Port clashes on this machine:** XAMPP's Apache answers `localhost:443`, something else answers 5432, and Windows reserves 8080 and 55432. None affect a real VM. To rehearse production here, publish Caddy on other ports through a compose override.

## 5. Facts worth knowing

- **Never burst SACHET polygon downloads.** It blocks the IP with 403s for hours. The poller is throttled and has the area-name fallback. It is blocked here too, so 3 live tests skip.
- **Live runs find what fixtures can't.** Six real bugs appeared only on real data, after 252 tests were passing. See `docs/gates/A-G3.md`. Run a real drill after any change to planning.
- **Forecast rain cells are about 3,000 km² and are not road closures.** Only `routing/repository.py`'s road-blocking rule decides what blocks a route. A real zone over 5,000 km², such as a GDACS cyclone swath, is a warning too: a route may cross it, but a person decides.
- **Draw drills over a town with a census tag,** or the plan correctly stops at "no census population". Tenali and Vijayawada work. Coverage is about 2.7% of settlements.
- **Gemini:** models get retired or overloaded often. The fallback chain handles this; `LLM_MODEL` pins one.
- **AePDS responses are about 50 MB** and can take minutes, one commodity at a time, every 6 h. 36 offices are still skipped and logged by name.
- **Valhalla limits that matter:** 400 km per matrix pair, 2,500 pairs, 20 locations per route. `infra/valhalla/raise_limits.py` must run after a first build, or real hazard polygons are rejected.
- **Next 16:** MapLibre's worker is copied to `frontend/public/maplibre` on `npm install`. Don't remove that step.
- **Browser tests** are in `frontend/tests/e2e`. Run `npm run test:e2e` in `frontend`, with `OPERATOR_TOKEN` set so test duty officers and drivers can be approved. `ENROLMENT_CODE` is only used by the legacy direct vehicle-registration endpoint. `E2E_ROUTING=1` adds the drill, and `E2E_MAILPIT_URL` adds email. CI runs them without the road map.
- **Next keeps the page you left mounted but hidden,** so a test can find two of the same field. Use `locator("visible=true")`.
- **Simulated vehicles** are always named `TEST-SIM-GPS`. `backend/tests/tools/sim_driver.py` drives one for a test; set `ENROLMENT_CODE` first, and above about 500 m/s it jumps past the 300 m arrival circle and never reaches a stop. `backend/tests/tools/demo_fleet.py` is the presentation fleet: on only with `DEMO_FLEET=true` and compose profile `demo`, mounted rather than built into the image, and it cuts each route at its stops so speed can't skip one. Turn it off with `stack.ps1 demo-off` before real use. See contract request 006.
- **A plan offers the solver only the nearest 12 vehicles** (`PLANNER_MAX_VEHICLES`), fewer if there are many sites. Vehicles and sites together must stay under Valhalla's 2,500 matrix pairs, or the plan fails as "routing service unavailable".
- **Orders are cut to fit most of the fleet** (`planner.part_size_kg`). Sized for the largest truck, a part fits only that truck: an 8-tonne truck beside a 12-tonne one got nothing, and goods went unserved.
- **A truck with a real person goes first.** The solver pays 6 hours to start a fully simulated one. A driver who accepted a duty officer's request for that place goes before everyone (`telemetry/requests.py`, `planner.start_cost_s`).
- **Evacuations** (`graph/evacuation.py`, contract request 007) are ordinary plans and routes with `routes.kind = 'evacuation'`, so the Route Monitor and driver screen need no special case, apart from `reroute`, because there are no goods to replan. The way out avoids every active zone, not only closures.
- **Map tiles come through `frontend/src/app/tiles`,** not straight from the browser. OpenStreetMap answers 403 to a browser that hides its Referer, so this sends a proper User-Agent, and falls back to CARTO if refused.
- **Plans approve themselves by rule** (`agents/approval.py`, contract request 008). Anything unclear goes to a person. `recommend_human_review` was true on 58 of 62 real plans until its prompt was narrowed, so check it stays rare.
- **Every test drill uses Gemini's free quota.** Several in a row brought HTTP 429 on all models. The plan continues with labelled rule-based text.
- **Auto-plans need a settlement.** An alert gets a plan only once it is active and covers a place in `v_places`, and it waits while facilities are loading. Drills always get one.
- **Drills are silent.** They record each message as not sent, even with Twilio configured, unless `DRILL_SEND_MESSAGES=true`.
- **`backend/requirements/constraints.txt` pins every version** for the image and CI. Refresh it when a dependency changes.

## 6. Ideas, not commitments

- WorldPop for population, which needs a contract patch.
- Re-sourcing when a supplier is cut off after dispatch; today those goods are escalated.
- Scheduling the next wave automatically; today it is shown to the human.
- A second brain worker, which needs real locks around crash recovery.
- A public source for shelter capacity, so demand isn't split evenly across relief points.
- Named operator accounts, so an approval proves who made it rather than who was typed. See "What is not covered" in `docs/security.md`.
