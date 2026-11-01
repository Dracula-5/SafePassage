# SafePassage testing checklist (2026-11-01)

Details and evidence: [FINAL_TESTING_AND_DEPLOYMENT_READINESS.md](FINAL_TESTING_AND_DEPLOYMENT_READINESS.md). `[x]` = observed passing. `[~]` = partly. `[ ]` = not done here.

## Infrastructure
- [x] PostGIS, Redis answer; migrations apply (also on a fresh database)
- [x] Valhalla routes between AP towns (after rebuild); hazard-polygon limits kept
- [x] API, ingestion worker, brain worker running; heartbeats fresh; 6 data sources current
- [x] SSE stream delivers history and live events, directly and through the web proxy
- [x] Every GET endpoint swept: no 500s; private ones refuse anonymous callers
- [x] `stack.ps1 up` restarts everything cleanly
- [x] Postgres and Redis down: API stays up, `/health` degraded, no leaks, nobody signed in

## Accounts and roles (production-mode API)
- [x] Duty officer and driver sign-up pending; no cookie; cannot sign in or read plans
- [x] Supplier starts at once
- [x] Nobody without authority can approve; operator token approves the first officer; officer approves a driver
- [x] Cookie HttpOnly, SameSite=Lax, Secure over HTTPS; cross-site request refused
- [x] Sign-out kills the cookie; lockout after 10 wrong passwords with `Retry-After`; access removal ends a session
- [x] Sign-up flood rate limited; passwords scrypt-hashed; sessions stored as hashes
- [x] Driver and supplier refused on duty officer pages and APIs
- [x] Sign-up has no Operator Code or Enrolment Code field
- [x] Unsafe production settings refused at startup (8 cases)

## Duty officer app (all 10 pages)
- [x] Load, no page errors, no sideways scroll at 375x812, 390x844, 768x1024, 1366x768, 1920x1080
- [x] Plans: failed/unreachable wording, approval confirmation, cancel sends nothing, one request on confirm
- [x] Activity & AI shows real decision history; Safety checks counts all Critic steps
- [x] Keyboard: sign-in, menu; status shown in words
- [~] Fleet, Directory, Evacuations, simulation tools: rewritten tests type-checked, not executed
- [x] Session expiry returns to sign-in

## Driver and supplier apps
- [x] Driver registration, six screens, 44 px touch targets, phone layout
- [x] Supplier registration, screens, phone layout
- [x] Offline GPS queue unit tests (6)
- [ ] Real phone GPS in Andhra Pradesh; Telugu voice on a phone

## AI and routing pipeline (labelled drill over Kurnool)
- [x] Alert, Sentinel, Needs, Procurement (shortfalls reported), Route Planner, Critic
- [x] Approval, conflicting second decision refused, re-check before dispatch
- [x] Driver assignment, Telugu instructions, PO PDFs, no messages sent
- [~] Simulated GPS: stops 1/10 and 2/10 only (simulator step too large for the rest)
- [x] New hazard on the route: replan, Critic, auto-approval of the re-route, dispatch
- [x] Honest "unreachable" under real cyclone hazards; honest "routing unavailable" on a router timeout
- [x] Automated chaos suites: router, Gemini, Redis, worker crash, duplicates, offline driver, evacuation

## Build and quality gates
- [x] `tsc`, ESLint, `next build` (20 routes), `audit_runtime.py` 0 findings
- [x] `npm audit` and `pip-audit`: nothing known
- [x] Backend 553 passed, 3 skipped (SACHET 403), 1 test flaw fixed; Playwright 26 passed, 13 skipped

## Deployment
- [x] Backend and frontend production images build; backend starts read-only, no capabilities
- [x] Caddyfile validates; keep-alive race fixed
- [x] No secrets in tracked files
- [ ] Full compose stack with TLS on the target VM
- [ ] Backup and restore exercised
- [ ] Contract requests 002 to 005 reviewed
