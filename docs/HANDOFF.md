# ReliefRoute: handoff (status on 2026-10-31)

Start here when continuing the project. The rules are in `docs/MASTER_PROMPT.md`, setup is in `README.md`, and per-gate evidence is in `docs/gates/`.

## 1. Where things are

| Branch | Contents |
|---|---|
| `integration/gate3` | **Everything.** Work continues here |
| `feat/brain` | Gate 2 plus the merged PRs; the GitHub default branch |
| `main` | Gate 2, without `infra/demo/seed_demo_water.py`. Not updated: nothing is pushed to `main` without a PR |
| `feat/senses-face` | B's work up to Gate 2 only |

Tags: `phase0-contracts`, `phase1-routing`, `phase1-senses`. Gate 2 and 3 are not tagged because they aren't fully verified (see §3).

One worker carried both roles through Gates 3 and 4, on one integration branch with commits scoped per owner. If two instances work in parallel again, go back to separate branches: `contracts/ownership.txt` still defines who owns what.

`contracts/` is frozen. **Two contract requests are pending human review:**

- **002** (`contracts/requests/A-plan-and-admin-views.md`): two read-only views, added as owner migrations.
- **003** (`contracts/requests/A-security-hardening.md`): the access rules, new endpoints and limits. `openapi.yaml` does not describe these until it is applied.

## 2. Gate status

| Gate | Status |
|---|---|
| 0 Contracts and skeleton | ✅ |
| 1 Live data, map, routing | ✅ |
| 2 Pipeline, approval, driver and supplier apps | 🟡 Proven live end to end, except the two items in §3 |
| 3 Closed loop and edge cases | 🟡 Built and proven live with labelled simulated vehicles |
| 4 Polish, docs, deploy | 🟡 Docs done. Security reviewed and hardened. Production stack rehearsed end to end on this machine. Not deployed |

Tests at the last run: **397 passed, 3 skipped** (SACHET rate-limiting this IP), on real PostGIS, Redis and Valhalla. PWA queue: 6 passed. Self-audit: 0 findings. Evidence is in `docs/gates/A-G3.md` and `docs/gates/A-G4-security.md`.

The local database holds live data only: this session's drills, plans and test vehicles were cleared. Their PO PDFs, labelled DRILL, remain in `~/reliefroute-data/documents/`.

## 3. What is left, and who it needs

| Item | Needs |
|---|---|
| Live Gemini run: triage, brief, **Telugu translation** | A key in `backend/.env`, then restart the brain worker. Replans have never run with a live LLM |
| Telugu voice on a phone | The key, and a phone with a Telugu speech pack |
| Real-phone GPS | A teammate **inside Andhra Pradesh**, through a tunnel. The road graph covers AP only, and vehicles over 200 km from the relief points are ignored |
| Real water stock | A supplier check-in at `/portal`. `infra/demo/seed_demo_water.py` was not run here; it stays labelled, and running it means disclosing it |
| Deploy | A VM with 8 GB RAM, a domain name pointing at it, and a Vercel account. Then fill in `infra/deploy/.env` and follow `docs/security.md` |
| Langfuse dashboards, SMS and WhatsApp | Langfuse and Twilio keys. The code is in place; messages are recorded as not sent until then |
| Review contract requests 002 and 003 | The human owner |
| Merge into `main` | The human owner. Ideally a PR from `integration/gate3` |

Once Gemini and a real phone are verified, add the output to the gate reports and tag `phase2-*` and `phase3-*`.

## 4. Running on this machine (Windows)

The project is in `C:\Users\dheer\OneDrive\Desktop\safepassage`. It sits in a OneDrive folder, which syncs `node_modules` and `.git`; if that causes file-lock errors, move it out.

```powershell
.\infra\dev\stack.ps1 up        # containers, migrations, API, both workers, dashboard
.\infra\dev\stack.ps1 status
.\infra\dev\stack.ps1 down      # keeps the data
```

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
- **Forecast rain cells are about 3,000 km² and are not road closures.** Only `routing/repository.py`'s road-blocking rule decides what blocks a route.
- **Draw drills over a town with a census tag,** or the plan correctly stops at "no census population". Tenali and Vijayawada work. Coverage is about 2.7% of settlements.
- **Gemini:** models get retired or overloaded often. The fallback chain handles this; `LLM_MODEL` pins one.
- **AePDS responses are about 50 MB** and can take minutes, one commodity at a time, every 6 h. 36 offices are still skipped and logged by name.
- **Valhalla limits that matter:** 400 km per matrix pair, 2,500 pairs, 20 locations per route. `infra/valhalla/raise_limits.py` must run after a first build, or real hazard polygons are rejected.
- **Next 16:** MapLibre's worker is copied to `frontend/public/maplibre` on `npm install`. Don't remove that step.
- **Simulated vehicles** come from `backend/tests/tools/sim_driver.py` and are always named `TEST-SIM-GPS`. It never runs at runtime. Set `ENROLMENT_CODE` first. Above about 500 m/s it jumps past the 300 m arrival circle and never reaches a stop.
- **Drills are silent.** They record each message as not sent, even with Twilio configured, unless `DRILL_SEND_MESSAGES=true`.
- **`backend/requirements/constraints.txt` pins every version** for the image and CI. Refresh it when a dependency changes.

## 6. Ideas, not commitments

- WorldPop for population, which needs a contract patch.
- Re-sourcing when a supplier is cut off after dispatch; today those goods are escalated.
- Scheduling the next wave automatically; today it is shown to the human.
- A second brain worker, which needs real locks around crash recovery.
- A public source for shelter capacity, so demand isn't split evenly across relief points.
- Skipping auto-plans for forecast cells over the sea. They end as "no safe relief point" and fill the plan list; with Gemini on, each also costs a triage call.
- Named operator accounts, so an approval proves who made it rather than who was typed. See "What is not covered" in `docs/security.md`.
