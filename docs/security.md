# ReliefRoute security

For whoever deploys or operates ReliefRoute. It says what is protected, how to deploy safely, what to do when something goes wrong, and what is not covered.

The system moves real goods towards people in a disaster, so the question that matters most is not "can someone read data?" but **"can someone make a truck go to the wrong place, or stop one going to the right place?"**

## What could go wrong, and what stops it

| Threat | What stops it |
|---|---|
| A stranger registers a phone as a truck and is given relief goods | Registering needs the enrolment code. Replans auto-approve vehicles, so this gate matters most |
| Someone presses Approve, or fakes a drill | Operator token on every action that changes anything |
| A public page shows drivers' names and where trucks are | Operator token on those reads; a device can only see its own route |
| A drill tells real suppliers to prepare goods | Drills send no SMS or WhatsApp at all unless `DRILL_SEND_MESSAGES=true` |
| A fake supplier claims stock that does not exist | New suppliers are unverified, and any plan using one needs a human, who sees that |
| A lost or stolen phone | `POST /api/vehicles/{id}/revoke` ends its token. Planning skips it, and its route is replanned as offline |
| A broken phone asks for a new route every minute | Off-route and stalled replans pause after 6 an hour per vehicle, and the human is told. Hazards are never held back |
| A hostile name in a form reaches a PDF, a prompt or a screen | Names reject markup and control characters. PDFs escape them, screens escape them, and the dashboard has a content security policy |
| Text in an alert or a name tricks the LLM | Prompts mark it as data. More importantly, the LLM cannot move goods: solvers plan, and Python decides who must approve |
| A flood of requests | Rate limits per address, a 1 MB body limit, a cap on live connections, row caps on list endpoints |
| A hole in the API reaches the server | Services connect as a Postgres role with table access only. Containers run as non-root with a read-only filesystem and no Linux capabilities |
| A secret leaks through logs or an error | Logs and stored errors are scrubbed, and error responses give no details. API keys never reach the browser |
| Someone deploys it badly | In production the API refuses to start if a secret is missing or weak, and `APP_ENV=prod` is an error rather than a silent downgrade |
| Two workers run at once | A Postgres lock keeps one active, and a second waits as a hot standby |
| A bad event blocks the queue | Parked after 5 failed deliveries and counted in `/api/brain/health`. An outage is not the event's fault, so those are retried for as long as it lasts |
| A worker dies silently | Heartbeats: a dead worker shows as stale on the dashboard |
| The database is lost | A verified daily backup. See restoring below |

## Who can do what

| Credential | Header | Held by | Allows |
|---|---|---|---|
| Operator token | `X-Operator-Token` | Duty officers | Drills, plans, approvals, revocation, and operational reads |
| Stream ticket | `?ticket=` | The dashboard, automatically | Reading the live stream for 5 minutes. It is safe in a URL, unlike the token |
| Enrolment code | `X-Enrolment-Code` | Drivers, told by phone | Registering a vehicle |
| Device token | `X-Device-Token` | One phone | That vehicle's GPS upload and its own route |
| Supplier token | `X-Supplier-Token` | One supplier | That supplier's stock reports |
| Twilio signature | `X-Twilio-Signature` | Twilio | The WhatsApp webhook |

Hazards, facilities, source health, `/portal` and `/driver/` are public on purpose. Device and supplier tokens have 256 bits of randomness and only their SHA-256 hashes are stored.

## Deploying

The API refuses to start in production without the first three.

1. Fill in `infra/deploy/.env` from `.env.example`. Make each secret with `openssl rand -hex 32`.
2. Point `DOMAIN` at the VM. Caddy gets the HTTPS certificate by itself. Only ports 80 and 443 are opened.
3. Set `CORS_ORIGINS` to the dashboard's `https://` address.
4. Open the VM's firewall for 80 and 443 only.
5. Copy `/backups` off the VM on a schedule. A backup on the same disk dies with the disk.
6. Deploy the dashboard. It holds no secrets: `NEXT_PUBLIC_*` values are public, so never put a token in one.

On Vercel, build with `NEXT_PUBLIC_API_BASE` set to the real API address, because the dashboard's security policy allows calls only to that address.

Locally, `.\infra\dev\stack.ps1 up` creates the token and code in `backend\.env`, and containers listen on `127.0.0.1` only. A tunnel for a phone test therefore exposes a protected API, but still close it afterwards.

## When something goes wrong

**A phone is lost, or a vehicle nobody recognises appears**

```bash
curl -X POST https://$DOMAIN/api/vehicles/$VEHICLE_ID/revoke -H "X-Operator-Token: $TOKEN"
```

Its token stops working at once and planning skips it. If it was driving, the route is replanned after about 5 minutes, when it counts as offline.

**A supplier cannot be trusted**

```bash
curl -X POST https://$DOMAIN/api/suppliers/$SUPPLIER_ID/verify -H "X-Operator-Token: $TOKEN" \
     -H "Content-Type: application/json" -d '{"verified": false}'
```

The same call with `true` vouches for a supplier. Stock they already reported stays until they report again, so check open plans.

**A token or code leaked**

Change `OPERATOR_TOKEN` or `ENROLMENT_CODE` in `infra/deploy/.env`, then run `docker compose ... up -d api`. Stream tickets stop working at the same moment, because they are signed with the operator token. Duty officers are asked for the new token on their next action. Registered phones are not affected by a new enrolment code.

**A database password leaked**

Change `APP_DB_PASSWORD`, then run `docker compose ... up -d`. Migrations update the role on the way up.

**A worker shows as stale on the dashboard**

Run `docker compose ... logs brain`. If it died, `restart: unless-stopped` brings it back, and it picks up anything left unfinished. Check `/api/brain/health` for parked events.

**Restoring the database**

```bash
docker compose ... stop api brain ingestion
docker compose ... run --rm --no-deps --entrypoint sh backup -c 'pg_restore --clean --if-exists --no-owner -d "$PGDATABASE" /backups/<file>.dump'
docker compose ... run --rm migrate
docker compose ... start api brain ingestion
```

The restore replaces what is there. `migrate` puts the service role's access back, and the workers pick up plans that were in progress.

## What is not covered

- **A single shared operator token.** `decided_by` is what the operator typed, so an approval record shows who was named, not who proved it. Separate accounts need a login system.
- **The supplier portal is open to anyone.** That is deliberate, so a real supplier can check in during a disaster. Limits and the unverified flag contain it, but fake entries can still appear for a human to reject.
- **GPS can be faked by a registered phone.** The token proves which phone is reporting, not where it is.
- **WhatsApp replays.** Twilio's signature has no timestamp, so a captured message could be resent. It only repeats the same stock figures.
- **Encryption at rest** depends on the VM's disk. Phone numbers and positions are stored in plain form in Postgres.
- **Denial of service at the network level.** The limits here protect the application, not the VM's connection. Put Cloudflare or similar in front for a real event.
- **An independent audit has not been done.** This is a review by the people who built it.
