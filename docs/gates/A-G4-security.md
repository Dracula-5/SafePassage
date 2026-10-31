# Gate 4 report: security review and production rehearsal

Run on 2026-10-31, on branch `integration/gate3`, at the owner's request to complete security and make the system hard to misuse. One worker carried both roles. What was built and how to operate it is in [security.md](../security.md); this is the evidence.

## Test results

```
397 passed, 3 skipped, 4 warnings in 439.39s
SKIPPED [3] tests\brain\live_sources.py:37: SACHET polygon endpoint is rate-limiting this IP
audit_runtime.py: 0 finding(s)
driver-pwa queue tests: 6 passed
pip-audit: no known vulnerabilities (86 packages)
npm audit --omit=dev: 0 vulnerabilities
git history (all branches): no secrets found; only .env.example files tracked
```

The new tests are in `tests/senses/test_security.py` and `tests/brain/test_security_brain.py`.

**GitHub Actions passed all 5 jobs on Linux from a clean checkout** ([run 41183776200](https://github.com/Dracula-5/SafePassage/actions/runs/41183776200)): backend, frontend, driver app, deploy files and secrets. The first run failed the frontend type check, because Next's generated types are missing from a fresh checkout; the workflow now generates them. CI has no road graph, so tests that need Valhalla skip there and must be run locally.

## What the review found

| Severity | Problem | Fix |
|---|---|---|
| Critical | Anyone could register a vehicle, and replans assign vehicles with no human | Enrolment code |
| Critical | Postgres and Redis listened on every network interface, with a known password and no password | Bound to `127.0.0.1` |
| High | With Twilio on, a drill would message real suppliers and drivers | Drills are silent unless turned on |
| High | A supplier name from the public portal went straight into PDF markup | Escaped. Proven below |
| High | Driver names, live positions, routes and orders were public | Operator token; a device sees only its own route |
| High | Production could start with no token at all | The API refuses to start |
| High | Services ran as a Postgres superuser | A role with table access only |
| Medium | No rate limits, body limit or connection cap | Added |
| Medium | No way to revoke a lost phone or a bad supplier | Added |
| Medium | The FIRMS key could reach logs through httpx's request log | Scrubbed, and httpx quietened |
| Medium | A failed PDF marked a dispatched plan as failed while trucks were on the road | Audited; the plan stands |
| Medium | An event whose handler always failed retried forever; losing Redis's data stopped the workers | Parked after 5 tries; groups are recreated |
| Medium | No way to tell a dead worker from a quiet day | Heartbeats on the dashboard |
| Medium | A faulty phone could request a replan every minute | Capped at 6 an hour, never for hazards |
| Low | Non-ASCII secrets caused a 500, not a 401 | Compared as bytes |
| Bug | The WhatsApp webhook needed `python-multipart`, which was not installed, so it would crash on its first real message | Parses the form directly |
| Bug | `TWILIO_AUTH_TOKEN` was read from the process environment only, so `.env` had no effect | Read through settings |

**The PDF hole, on the old code:**

```
CRASHED '</para><b>': IndexError
CRASHED '<img src="C:/.../rr_fake_secret.png" .../>': UnidentifiedImageError
```

The second shows reportlab opening a local file named in a supplier's name. With a valid image, it would have embedded it.

## Production rehearsal

The real stack ran on this machine: real image, Caddy with HTTPS, private networks, read-only containers, the non-superuser role. The real database was restored into it with the commands in `security.md`, and then all of it was torn down. Throwaway secrets were deleted.

**From outside, through Caddy:** 27 of 28 checks passed. Docs hidden, HTTP redirected, operational reads and writes refused, 3 MB body refused, and the rate limit held against a forged `X-Forwarded-For`. The one failure was another Postgres on this PC answering port 5432. The production database publishes no ports, is only on the private network, and that port rejected its password.

```
docker exec ... id -un                  relief
touch /app/x                            Read-only file system
touch /data/documents/w                 ok
database has a route out                no: Network is unreachable
redis-cli ping                          NOAUTH Authentication required
service connections                     reliefroute_app (rolsuper = f)
```

**A drill on the restored real data, with labelled simulated vehicles:**

```
13:10:02 drill 201 DRILL: Rehearsal: Krishna flood
13:10:10 plan 05ea358f awaiting_approval
13:10:12 approve with no token: 401
13:10:12 approve as operator: 200
13:10:14 dispatched: 5 orders on 1 route(s); ['315 km']
13:10:16 stream status 200 text/event-stream
13:10:16 first event after 0.0s
```

5 purchase-order PDFs were written to the volume. A fresh backup read back with 229 objects, 38 MB.

**A truck revoked on its route:**

```
13:16:34 revoking the truck on the route: 200
07:51:10 replanner Deviation on TEST-SIM-GPS-B: offline
07:51:10 replanner offline: 5 order(s) to re-route, 0 line(s) stranded on the vehicle
07:51:16 supervisor Auto-approved: re-route of goods already approved
07:51:16 dispatcher 3 order(s) and 1 route(s) committed
```

Service logs held no errors, permission failures or read-only failures. Times in the first log are IST, in the second UTC.

**Real Chrome, against the dev stack with protection on:** no policy violations on the dashboard, driver app or portal. Live stream connected. A script injected into the portal did not run. Cancelling the token prompt left the public hazard map working.

## Bugs only the rehearsal found

Every test passed before these appeared.

1. **Caddy could not start.** Docker gave the API the fixed address reserved for Caddy. Fixed with a separate range for automatic addresses.
2. **`migrate` failed on a database first migrated on Windows.** Backslash paths made Linux re-run the whole schema. Keys are now always forward slashes, and old rows are repaired.
3. **The registration limit could lock out a depot.** 20 an hour per address is too few when drivers share one network. Raised to 60; the code is what stops guessing.
4. **The documented restore command would have re-run the backup.** That service has its own entrypoint. Corrected and run.
5. **The token prompt nagged.** A wrong or cancelled token prompted repeatedly. It now stays quiet until a button is pressed.

## Not verified

- **A real domain and a real certificate.** Caddy used its local CA for `localhost`.
- **The off-route replan in production configuration.** The simulator was too fast to reach a stop in that run. The offline replan writes the same tables and passed; off-route passed live in `A-G3.md` and in tests.
- **Ingestion in production configuration.** It was not started, to avoid a second poller hitting SACHET. Its bulk load ran as the service role only in `test_the_service_role_can_run_the_system`.
- **An independent security audit.** This was a review by the people who built it.
- **Gemini and a real phone in Andhra Pradesh,** as before.
