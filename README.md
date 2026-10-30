# ReliefRoute

ReliefRoute watches live disaster alerts for Andhra Pradesh, works out what an affected area needs, finds real stock, plans truck routes that stay clear of the hazard, and dispatches them once a human approves. If a driver leaves the route, goes silent, or the hazard spreads across the road ahead, it replans on its own.

Built for a Gen-AI buildathon (Theme 2: Climate, Resource Infrastructure & Global Supply Resilience). Nothing is mocked at runtime: every number on screen comes from a live source, an official open dataset, or a labelled fallback.

**LLMs reason, solvers compute.** Gemini triages alerts, briefs the human and translates driver instructions. OR-Tools plans the routes and Shapely verifies them. The LLM never supplies a coordinate, distance or stock figure.

![Dashboard: a planned route bending around a flood drill at Tenali, with the agent timeline and approval panel](docs/images/dashboard.png)

## How it works

```mermaid
flowchart LR
  subgraph Sources[Live sources]
    S1[NDMA SACHET alerts]
    S2[GDACS]
    S3[Open-Meteo rainfall]
    S4[AePDS PDS stock]
    S5[OSM roads, facilities, census tags]
    S6[Supplier check-ins]
    S7[Driver phones: GPS]
  end
  subgraph Senses[Senses: deterministic, no LLM]
    I[Pollers with breakers and staleness tags]
    T[Telemetry API]
    P[Supplier portal]
  end
  DB[(PostGIS)]
  R{{Redis Streams}}
  subgraph Brain[Brain: LangGraph with Postgres checkpoints]
    G[Sentinel, Needs, Procurement,<br/>Route Planner, Critic, Brief]
    H{Human approval}
    V[Re-check, then Dispatcher]
    M[Route Monitor]
  end
  VH[Valhalla road graph]
  OR[OR-Tools VRP + Shapely]
  LLM[Gemini]
  UI[Dashboard: Next.js + MapLibre]
  PWA[Driver PWA: voice instructions]

  S1 & S2 & S3 & S4 & S5 --> I --> DB
  S6 --> P --> DB
  S7 --> T --> DB
  I & T & P --> R
  R --> G --> H --> V
  R --> M -->|deviation| G
  G <--> VH
  G <--> OR
  G <--> LLM
  V --> DB
  DB --> UI
  R --> UI
  V --> PWA
  UI -->|approve| H
```

1. **Sentinel** triages an alert. A severe flood-type hazard or an operator's drill starts a plan by itself.
2. **Needs** turns census population in the zone into demand per relief point, using Sphere humanitarian standards.
3. **Procurement** finds the nearest real stock and splits it across suppliers, or escalates the shortfall.
4. **Route Planner** solves pickups and deliveries around hazard polygons, one wave of work at a time.
5. **Critic** independently re-checks every route against current hazards and rejects it with the exact violation, up to three times.
6. **Human approval** is required for anything high-stakes. The plan is re-checked after approval, so a stale one is never dispatched.
7. **Dispatcher** writes orders and routes idempotently, translates driver instructions, and creates purchase-order PDFs.
8. **Route Monitor** follows each truck by GPS and closes the loop.

See [docs/brain.md](docs/brain.md) and [docs/senses.md](docs/senses.md) for the detail, and [docs/demo-script.md](docs/demo-script.md) for the live demo.

## Data sources

| Source | Used for | Status |
|---|---|---|
| [NDMA SACHET](https://sachet.ndma.gov.in/) (CAP) | Official alerts | Live. When SACHET rate-limits polygon downloads, alerts are placed on OSM boundaries by their area names, and labelled as such |
| [GDACS](https://www.gdacs.org/) | Cyclones and floods | Live |
| [Open-Meteo](https://open-meteo.com/) | Forecast rainfall, classified with IMD 24 h categories | Live |
| [AePDS](https://aepos.ap.gov.in/) | Real PDS rice and dal stock per mandal | Live, every 6 h. Pickup points are approximate because shop locations are not published. About 36 offices are skipped because their names can't be matched with certainty |
| OpenStreetMap ([Geofabrik](https://download.geofabrik.de/asia/india.html)) | Roads, facilities, admin boundaries, census population tags | Official extract. Overpass is tried first for facilities |
| Supplier portal, WhatsApp | Stock reported by real suppliers | Portal live. WhatsApp needs Twilio credentials |
| Driver PWA | Real phone GPS | Live |
| NASA FIRMS | Fires | Code ready, off until `FIRMS_MAP_KEY` is set |
| IMD direct, CWC/India-WRIS, Data.gov.in godowns | | **Not wired.** IMD's rainfall categories are applied to Open-Meteo instead |

Sources that are down are shown as `DATA STALE` on the dashboard, and existing rows are kept. Nothing is filled in with guesses. There is no public source for drinking-water stock, so water only appears through supplier check-ins; until one exists, plans escalate it as a shortfall.

## Run it

You need Docker Desktop with 6 to 8 GB of RAM, Python 3.12 with [`uv`](https://docs.astral.sh/uv/), Node 22, and a [Gemini API key](https://aistudio.google.com/apikey). Allow about 12 GB of disk. The map data lives outside the repo, in `~/reliefroute-data`.

```bash
git clone https://github.com/Dracula-5/SafePassage.git && cd SafePassage
```

**1. Build the map data** (about 15 minutes; needs `osmium-tool`).

```bash
infra/valhalla/build.sh && infra/osm/extract_facilities.sh && infra/osm/extract_places_admin.sh
```

No `osmium` (Windows)? Run the same scripts in a throwaway container from PowerShell:

```powershell
docker run --rm -e DATA_DIR=/data -v "$HOME\reliefroute-data:/data" -v "${PWD}\infra:/infra:ro" debian:bookworm-slim sh -c "apt-get update -qq && apt-get install -y -qq --no-install-recommends bash curl ca-certificates osmium-tool && bash /infra/valhalla/build.sh && bash /infra/osm/extract_facilities.sh && bash /infra/osm/extract_places_admin.sh"
```

**2. Start the services.** The first Valhalla start builds the road graph: about 5 minutes on Linux and macOS, 20 or more on Windows.

```bash
docker compose --profile routing up -d
python3 infra/valhalla/raise_limits.py && docker compose restart valhalla    # required
```

If Windows refuses port 55432 (`bind: An attempt was made to access a socket...`), it has reserved that port. Create a root `.env` with `DB_HOST_PORT=15432` and use that port in `backend/.env`.

**3. Install and configure.**

```bash
uv venv --python 3.12 ~/reliefroute-venv
uv pip install --python ~/reliefroute-venv/bin/python -e contracts/python \
  -r backend/requirements/brain.txt -r backend/requirements/senses.txt -r backend/requirements/dev.txt
cat env/senses.env.example env/brain.env.example > backend/.env     # then set GEMINI_API_KEY
(cd frontend && npm install && cp .env.example .env.local)
```

Keep the two `DATABASE_URL`/`REDIS_URL` pairs in `backend/.env` down to the first one, which has the right ports. On Windows the interpreter is `~/reliefroute-venv/Scripts/python.exe`.

**4. Run.** On Windows, one command starts everything and `down` stops it:

```powershell
.\infra\dev\stack.ps1 up
```

On Linux and macOS, from `backend/`, each in its own terminal, plus `npm run dev` in `frontend/`:

```bash
~/reliefroute-venv/bin/python -m app.core.migrate
~/reliefroute-venv/bin/uvicorn app.main:app --port 8000
~/reliefroute-venv/bin/python -m app.ingestion.worker
~/reliefroute-venv/bin/python -m app.graph.worker
```

| What | Where |
|---|---|
| Dashboard | http://localhost:3000 |
| Driver app | http://localhost:8000/driver/ |
| Supplier portal | http://localhost:8000/portal |
| API docs | http://localhost:8000/docs |

The AePDS stock download takes 5 to 10 minutes after the first start.

### Testing on a phone

A browser only gives GPS over HTTPS, and the road graph only covers Andhra Pradesh. A phone inside the state can use a temporary tunnel:

```bash
cloudflared tunnel --protocol http2 --url http://localhost:8000
```

Open `https://<tunnel>/driver/`. **Stop the tunnel afterwards:** the API is reachable by anyone with that URL unless `OPERATOR_TOKEN` is set.

Without a phone in the region, `backend/tests/tools/sim_driver.py` is a labelled test tool that drives a vehicle through the public API. Its vehicles are always named `TEST-SIM-GPS`.

## Tests

```bash
cd backend
TEST_DATABASE_URL=postgresql://reliefroute:reliefroute@localhost:55432/postgres \
TEST_REDIS_URL=redis://localhost:6380/15 VALHALLA_URL=http://localhost:8002 \
~/reliefroute-venv/bin/python -m pytest -q
```

Integration tests run against real PostGIS, Redis and Valhalla in throwaway databases, and skip when those URLs are unset. `tests/*/chaos/` breaks dependencies on purpose: closed ports, servers that hang, garbage from the LLM, workers dying mid-plan. `node --test driver-pwa/tests/queue.test.js` covers the phone's offline queue.

## Deploying

[infra/deploy/](infra/deploy/) has a backend image and a compose file that runs the whole backend on one 8 GB VM. The dashboard goes on Vercel with `NEXT_PUBLIC_API_BASE` pointing at it and `NEXT_PUBLIC_REGION_BBOX` set.

Before exposing it:
- Set `OPERATOR_TOKEN`. Drills, plan triggers and approvals then require it, and the dashboard asks for it once per session. Reading stays open.
- Set `CORS_ORIGINS` to the dashboard's address.
- `POST /api/vehicles` and `/portal` stay open so drivers and suppliers can sign up. New suppliers start unverified, and the human approver sees that.

## Repository

```
backend/app/agents     Needs, Procurement, Planner, Critic, LLM reasoning
backend/app/graph      LangGraph wiring, Dispatcher, Route Monitor, Replanner
backend/app/solvers    OR-Tools VRP, Shapely verification
backend/app/routing    Valhalla client
backend/app/ingestion  Pollers for every live source
backend/app/telemetry  Vehicle registration and GPS
backend/app/suppliers  Supplier portal
contracts/             Database schema, events and REST contract shared by both halves
frontend/              Dashboard
driver-pwa/            Driver app, served at /driver/
infra/                 Compose files, map data scripts, deployment
docs/                  Design notes, demo script, gate reports
```

## Known limits

- **Population is an estimate.** OSM census tags cover about 2.7% of settlements, so rural populations are undercounted. City figures are scaled by how much of their mandal the hazard covers, assuming even density. Every plan shows its coverage.
- **PDS stock locations are approximate:** a point inside the mandal, not the shops.
- **SMS and WhatsApp need Twilio credentials.** Without them, messages are recorded as not sent.
- **No re-sourcing after dispatch.** If a supplier is cut off once goods are on order, they are escalated to a human rather than bought elsewhere.
- **Waves aren't scheduled.** Goods beyond the first wave are shown to the human, not planned automatically.
- **One brain worker at a time.** Crash recovery assumes a single worker.
- **Telugu and Hindi voice** depend on the speech voices installed on the driver's phone.
