# ReliefRoute security

For whoever deploys or operates ReliefRoute. It says what is protected, how to deploy safely, what to do when something goes wrong, and what is not covered.

The system moves real goods towards people in a disaster, so the question that matters most is not "can someone read data?" but **"can someone make a truck go to the wrong place, or stop one going to the right place?"**

## What could go wrong, and what stops it

| Threat | What stops it |
|---|---|
| A stranger registers a phone as a truck and is given relief goods | Drivers cannot sign in or register a vehicle until an authorized duty officer approves the account. Replans auto-approve vehicles, so this gate matters most |
| Someone presses Approve, or fakes a drill | Only an approved duty officer account or the operator token can. New duty officer accounts also require approval |
| A public page shows drivers' names and where trucks are | Those reads need a duty officer; a device can only see its own route |
| A drill tells real suppliers to prepare goods | Drills send no SMS or WhatsApp at all unless `DRILL_SEND_MESSAGES=true` |
| A fake supplier claims stock that does not exist | New suppliers are unverified, and any plan using one needs a human, who sees that |
| A lost or stolen phone | `POST /api/vehicles/{id}/revoke` ends its token. Planning skips it, and its route is replanned as offline |
| A bad GPS fix, or a supplier registered out at sea, stops planning for everyone | Valhalla refuses a whole matrix over one unconnected point, so that point is marked unreachable and the rest still plan |
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
| Account | `rr_session` cookie | Everyone, after signing in | What that person's role allows, for 7 days or until they sign out |
| Operator token | `X-Operator-Token` | Administrators and scripts | Operational API access and approving the first duty officer account |
| Enrolment code | `X-Enrolment-Code` | Trusted callers of the legacy vehicle endpoint | Registering a vehicle through `POST /api/vehicles`; not used for web signup |
| Device token | `X-Device-Token` | One phone | That vehicle's GPS upload and its own route |
| Supplier token | `X-Supplier-Token` | One supplier | That supplier's stock reports |
| Twilio signature | `X-Twilio-Signature` | Twilio | The WhatsApp webhook |

Hazards, facilities and source health are public on purpose, and so is creating a supplier account. Passwords are hashed with scrypt, and session cookies are stored only as hashes and cannot be read by scripts. Ten wrong passwords lock an account for 15 minutes. Device and supplier tokens have 256 bits of randomness and only their SHA-256 hashes are stored.

## Deploying

[deploy.md](deploy.md) has the steps. What matters for security:

1. `infra/deploy/setup.sh` writes strong secrets into `infra/deploy/.env`. The API refuses to start without them.
2. `PUBLIC_URL` must start with `https://`. Caddy gets the certificate by itself.
3. Open the VM's firewall for 80 and 443 only, plus SSH.
4. Copy the `backups` volume off the VM on a schedule. A backup on the same disk dies with the disk.
5. The dashboard holds no secrets: `NEXT_PUBLIC_*` values are public, so never put a token in one.

Locally, `.\infra\dev\stack.ps1 up` creates the token and code in `backend\.env`, and containers listen on `127.0.0.1` only. Duty officers and drivers sign up without codes, but an approved duty officer (or the operator token) must approve them before they can sign in. A tunnel for a phone test therefore exposes a protected API, but still close it afterwards.

On a fresh deployment, approve the first duty officer with the operator token:

```bash
curl -X POST "$PUBLIC_URL/api/accounts/<ACCOUNT_ID>/approve" -H "X-Operator-Token: $OPERATOR_TOKEN"
```

After that, an approved duty officer can approve pending accounts in the dashboard's Team tab.

## When something goes wrong

**A phone is lost, or a vehicle nobody recognises appears**

In the dashboard, open Fleet and press Remove. From a script:

```bash
curl -X POST $PUBLIC_URL/api/vehicles/$VEHICLE_ID/revoke -H "X-Operator-Token: $TOKEN"
```

Its token stops working at once and planning skips it. If it was driving, the route is replanned after about 5 minutes, when it counts as offline.

**A supplier cannot be trusted**

In the dashboard, open Team and press Withdraw verification. From a script:

```bash
curl -X POST $PUBLIC_URL/api/suppliers/$SUPPLIER_ID/verify -H "X-Operator-Token: $TOKEN" \
     -H "Content-Type: application/json" -d '{"verified": false}'
```

The same call with `true` vouches for a supplier. Stock they already reported stays until they report again, so check open plans.

**A token or code leaked**

Change `OPERATOR_TOKEN` or `ENROLMENT_CODE` in `infra/deploy/.env`, then run `docker compose ... up -d api`. Existing accounts keep working. Check the Team tab for anyone who should not be there, and use Remove access: it ends their session at once.

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

- **Email stays off until a provider is set.** Until then an address is only what the person typed, and a forgotten password is reset by a one-time link that a duty officer makes in the Team tab. With a provider, people confirm their address and reset their own password. Confirming is recorded, not required: pending approval controls access for duty officers and drivers. Forgot-password answers the same whether or not an account exists.
- **The supplier portal is open to anyone.** That is deliberate, so a real supplier can check in during a disaster. Limits and the unverified flag contain it, but fake entries can still appear for a human to reject.
- **GPS can be faked by a registered phone.** The token proves which phone is reporting, not where it is.
- **WhatsApp replays.** Twilio's signature has no timestamp, so a captured message could be resent. It only repeats the same stock figures.
- **Encryption at rest** depends on the VM's disk. Phone numbers and positions are stored in plain form in Postgres.
- **Denial of service at the network level.** The limits here protect the application, not the VM's connection. Put Cloudflare or similar in front for a real event.
- **An independent audit has not been done.** This is a review by the people who built it.
