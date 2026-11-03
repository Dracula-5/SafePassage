# Connecting the Vercel web app to the backend on Render

The web app at <https://safepassage-frontend.vercel.app> never calls the API at a second address. It sends `/api/*`, `/portal/api/*`, `/portal/whatsapp` and `/health` to itself, and Next forwards them to `API_ORIGIN`. That keeps the sign-in cookie first-party and the content security policy at `connect-src 'self'`, so the backend needs no CORS settings.

**Why the site showed 404:** no backend was deployed, and `API_ORIGIN` was not set in Vercel, so Next had nothing to forward to and answered every API call with its own 404 page. Checked on 2026-11-03: `/` answered 200, `/api/status` answered 404, and neither `reliefroute-api.onrender.com` nor `reliefroute-api-free.onrender.com` exists yet (`x-render-routing: no-server`).

A Vercel build now stops with a clear message when `API_ORIGIN` is missing, and the app says "This site is not connected to its API server" instead of "The server answered 404".

## 1. Deploy the backend on Render

Pick one blueprint. Both deploy from the `main` branch.

| Blueprint file | What runs | Cost |
| --- | --- | --- |
| `render-free.yaml` | API, Postgres and Key Value only. No ingestion, planning or road router, so there are no live alerts, plans or routes. Enough to prove that sign-in and the dashboard connect. | Free. It sleeps when idle, and free Postgres expires after 30 days. |
| `render.yaml` | The whole system: API, ingestion and planning workers, Valhalla router, database, Redis, and the simulated fleet. | Paid plans. Check Render's prices first. |

1. In Render, go to **New > Blueprint**, pick this repository, and set the blueprint path to the file you chose.
2. Apply it, then wait until the API is live. Open `https://<api-service>.onrender.com/health`: it must answer `{"status":"ok","checks":{"postgres":"ok","redis":"ok"}}`.
3. Copy the API's address from its Render page: `https://reliefroute-api-free.onrender.com` for the free blueprint, `https://reliefroute-api.onrender.com` for the full one, unless Render added a suffix because the name was taken.
4. For the full blueprint only, set `GEMINI_API_KEY` on `reliefroute-brain` and `FIRMS_MAP_KEY` on `reliefroute-ingestion` in the Render dashboard.

### No Blueprint offered, or it asks for a card? Make the free API by hand

This builds the same thing as `render-free.yaml` from the dashboard, with no Blueprint. Put all three pieces in **one region**, such as Singapore: the private addresses below only work inside one region. Render allows one free database and one free Key Value per workspace.

1. **New > Postgres.** Name `reliefroute-free-db`, database `reliefroute`, user `reliefroute`, PostgreSQL 16, instance type **Free**. When it is ready, copy its **Internal Database URL**.
2. **New > Key Value.** Name `reliefroute-free-redis`, instance type **Free**, maxmemory policy **noeviction**, and no inbound IP addresses allowed. Copy its **Internal Key Value URL**, which starts `redis://red-`.
3. **New > Web Service.** Connect this GitHub repository, branch `main`, language **Docker**, instance type **Free**. Under **Advanced** set:
   - **Dockerfile Path:** `./infra/deploy/backend-free.Dockerfile`
   - **Docker Build Context Directory:** `.`
   - **Docker Command:** leave it **empty**. The image creates the tables, then starts the API. Render keeps quotes as literal text, so a command such as `sh -c "a && b"` fails with `not found` and status 127.
   - **Health Check Path:** `/health`
4. Add these **Environment Variables** to the web service:

   | Key | Value |
   | --- | --- |
   | `DATABASE_URL` | the Internal Database URL from step 1 |
   | `REDIS_URL` | the Internal Key Value URL from step 2 |
   | `REDIS_PRIVATE_NETWORK` | `true` (the private address has no password; without this the API refuses to start) |
   | `APP_ENV` | `production` |
   | `OPERATOR_TOKEN` | a random secret of at least 32 characters |
   | `ENROLMENT_CODE` | a random secret of at least 12 characters |
   | `PUBLIC_URL` | `https://safepassage-frontend.vercel.app` |
   | `REGION_BBOX` | `76.6,12.5,84.9,20.1` |
   | `REGION_NAME` | `Andhra Pradesh` |
   | `VALHALLA_URL` | `http://127.0.0.1:8002` (no router on the free plan, but the setting must exist) |
   | `DEMO_FLEET` | `false` |
   | `PORT` | `8000` |
   | `FORWARDED_ALLOW_IPS` | `*` |
   | `LOG_LEVEL` | `INFO` |

   To make the two secrets in PowerShell 7, run `[Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24))` once for each; it prints 48 characters. Keep them only in Render.
5. **Create Web Service.** The first build takes several minutes. The log should show the migrations, then `Application startup complete`. Then open `https://<service-name>.onrender.com/health` as in step 1.2, and carry on from step 1.3.

If the log says `Refusing to start in production`, it lists each setting to fix. If it says `permission denied to create extension`, the database user cannot add PostGIS: make sure the database was created on PostgreSQL 13 or later.

### The full live demo from a laptop, free

The free Render API has no data worker, planning worker, road router or simulated fleet, so its screens stay empty. For a presentation, the whole stack can run on a laptop, exactly as `.\infra\dev\stack.ps1 demo` runs it, and the Vercel site can reach it through a free Tailscale Funnel. The site is live while the laptop is on and Docker is running; otherwise it shows "Cannot reach the server".

1. Start the stack: `.\infra\dev\stack.ps1 demo`, then check <http://localhost:3000/health>.
2. Install Tailscale (`winget install Tailscale.Tailscale`, then approve the Windows prompt), run `tailscale login`, and sign in through the link it prints.
3. Run `tailscale funnel --bg 3000`. The first time, it prints a link to allow Funnel and HTTPS for your tailnet. Approve both, then run it again. It prints the fixed address, such as `https://itachiuchiha.tail9ec12e.ts.net`. `--bg` keeps the Funnel on across restarts, run by the Tailscale service, with no window to keep open.
4. In Vercel, set `API_ORIGIN` to that address for Production and Preview, then redeploy. To go back to Render later, set it back and redeploy.

On the laptop, Caddy answers any host name (`docker-compose.local.yml`), so the tunnel arrives under its own address. Accounts on the laptop are the local database's, not Render's. After a restart, `.\infra\dev\stack.ps1 present` brings Docker and the stack back, checks the Funnel, then checks the hosted site.

**Why not ngrok.** It was used first. Its free plan allows 1 GB and 20,000 requests a month, and on 2026-11-04 a dashboard left open overnight used up the gigabyte. From then on ngrok answered every call with a 403 (`ERR_NGROK_725`) until the monthly reset, so sign-in on the hosted site failed. A dashboard then moved about 50 KB every 5 seconds, plus 1.8 MB of hazard shapes each time an alert changed, all uncompressed. Caddy now compresses API answers, about six times less data, and leaves `/api/stream` uncompressed. `frontend/src/proxy.ts` still adds ngrok's `ngrok-skip-browser-warning` header, which is harmless anywhere else, in case ngrok is used again.

## 2. Point Vercel at it

1. In the Vercel project for `safepassage-frontend`, open **Settings > General** and check that **Root Directory** is `frontend`.
2. Open **Settings > Environment Variables** and add these to both **Production** and **Preview**:
   - `API_ORIGIN`: the address from step 1.3, starting with `https://`, with no trailing slash and no path.
   - `NEXT_PUBLIC_REGION_BBOX`: `76.6,12.5,84.9,20.1`
   Do not use a `NEXT_PUBLIC_` prefix for `API_ORIGIN`: only the server reads it.
3. Redeploy from **Deployments > ... > Redeploy**. The forwarding rules are fixed at build time, so saving the variable alone changes nothing.

## 3. Check it

- `https://safepassage-frontend.vercel.app/health` returns the same JSON as the API's own `/health`.
- `https://safepassage-frontend.vercel.app/api/status` returns JSON, not a 404 page.
- Sign up at `/signup` as a duty officer. In production a new duty officer waits for approval, so approve the first one with the operator token. Its value is in Render's `reliefroute-free-demo` or `reliefroute-shared` environment group. Never put it in Vercel or in git.

  ```sh
  API=https://reliefroute-api-free.onrender.com   # your API address
  curl -s "$API/api/accounts" -H "X-Operator-Token: $OPERATOR_TOKEN"            # find your account's id
  curl -s -X POST "$API/api/accounts/<id>/approve" -H "X-Operator-Token: $OPERATOR_TOKEN"
  ```

  From then on, approved duty officers approve everyone else on **Team & Access**.
- Sign in. The Command Center shows the live counts across the top and the Plans / Evacuations / Activity / Team panel beside the map. The pill at the top right reads **Live**.

## When something is wrong

| What you see | Cause | Fix |
| --- | --- | --- |
| The Vercel build fails with "API_ORIGIN is not set" | The variable is missing for that environment | Add it to Production and Preview, then redeploy |
| "This site is not connected to its API server (404)" | The deployment was built before `API_ORIGIN` was set | Redeploy |
| "The API server is not answering (502/503/504)" | The free API is asleep or still starting, or the address is wrong | Wait a minute and retry. Check the API's `/health` directly |
| The API's `/health` is ok, but `/api/status` answers 500 and the deploy log has no `migrations_done` line | The tables were never made: the image was built from a commit before `backend-free.Dockerfile` ran migrations | Deploy the latest commit with **Manual Deploy**, with the Docker Command empty |
| Deploy fails with `sh: 1: python -m app.core.migrate && …: not found` and status 127 | A quoted Docker Command: Render passes the quotes on, so the whole command becomes one word | Empty the Docker Command field and deploy again |
| Signing up works, but signing in says the account is pending approval | Production keeps new duty officers and drivers waiting | Approve the first one with the operator token, as above |
| "Live updates interrupted" now and then | A proxy along the way (Vercel's or Render's) closed the long-lived event stream | Nothing to do: the stream reconnects by itself and the numbers refresh every 5 seconds regardless |
| Email links point to the wrong site | `PUBLIC_URL` on Render differs from the web app's address | Set `PUBLIC_URL` to `https://safepassage-frontend.vercel.app` (both blueprints already do) |

Netlify can host the web app instead: see [deploy-render-netlify.md](deploy-render-netlify.md). For one machine with Docker, see [deploy.md](deploy.md).
