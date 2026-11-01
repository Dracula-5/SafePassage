# Deploying ReliefRoute

For whoever puts ReliefRoute online. Everything runs on one VM behind one address: one web app where duty officers, drivers and suppliers each sign in and land on their own screen. There is no separate frontend to host.

Budget about an hour the first time, most of it building the map data. Copying that data from a machine that already has it saves most of the wait.

## What you need

- A VM with **8 GB of RAM**, 40 GB of disk and Ubuntu 24.04, or similar. It must be a VPS you can log in to: ordinary shared web hosting can't run Docker.
- A **domain name** you can point at it. Phones only give GPS over HTTPS, so an IP address will not do.
- A **Gemini API key**. Without one the system still runs, on labelled rule-based steps, but drivers get English instructions.
- Optional: Twilio keys for SMS and WhatsApp, Langfuse keys for tracing, a FIRMS key for fire data, and an email provider's SMTP details.

## Steps

**1. Prepare the VM**

Install Docker, then open ports 80 and 443 and nothing else except SSH. Point the domain's DNS `A` record at the VM's address.

```bash
curl -fsSL https://get.docker.com | sh
git clone https://github.com/Dracula-5/SafePassage.git && cd SafePassage
git checkout integration/gate3      # or main, once this branch is merged
```

**2. Add the map data**

The quickest way is to copy `osm/` and `valhalla/` from a machine that has them into `/srv/reliefroute-data`. To build them on the VM instead:

```bash
sudo apt install -y osmium-tool
export DATA_DIR=/srv/reliefroute-data
sudo mkdir -p $DATA_DIR && sudo chown $USER $DATA_DIR
infra/valhalla/build.sh
infra/osm/extract_facilities.sh
infra/osm/extract_places_admin.sh
```

Step 3 writes `infra/deploy/.env`. Then build the road tiles, which takes about 5 minutes on Linux:

```bash
DC="docker compose -f infra/deploy/docker-compose.prod.yml --env-file infra/deploy/.env"
$DC up -d valhalla                  # wait until `$DC logs valhalla` shows it serving
python3 infra/valhalla/raise_limits.py && $DC restart valhalla
```

`raise_limits.py` is required: without it, real hazard polygons are rejected.

**3. Create the secrets and check the machine**

```bash
sh infra/deploy/setup.sh https://relief.example.org
nano infra/deploy/.env              # add GEMINI_API_KEY and any optional keys
```

`setup.sh` writes `infra/deploy/.env` with strong random secrets, then checks the RAM, the map data and the DNS. Run it again until it says "Ready". It never overwrites an existing `.env`. **Keep a copy of that file somewhere safe:** the database cannot be opened without it.

**4. Start everything**

```bash
docker compose -f infra/deploy/docker-compose.prod.yml --env-file infra/deploy/.env up -d --build
```

The API refuses to start if a secret is missing or weak, and `docker compose logs api` says which.

**5. Check it**

```bash
curl https://relief.example.org/health        # {"status":"ok",...}
```

Then open the address. A new database fills itself from the live sources. On the test run, hazards appeared within a minute, facilities took about 5 minutes and stock about 7. Alerts that arrive before the facilities wait for them, so the plan list stays honest. Plans only start for alerts that cover a settlement: forecast cells over the sea stay on the map without one.

Sign up at `/signup` as a duty officer. Approve the first duty officer through `POST /api/accounts/{account_id}/approve` using `X-Operator-Token` from `infra/deploy/.env`; after that, approved duty officers can approve other duty officers and drivers in the Team tab. Suppliers can sign up and use the portal immediately.

**6. Keep it safe**

Copy the `backups` volume off the VM on a schedule. [security.md](security.md) covers what to do if a phone is lost, a token leaks or the database must be restored.

## Turning on email

Any provider's SMTP works: Resend, SendGrid, Amazon SES, or a Gmail app password. Add these to `infra/deploy/.env`, then run `up -d`:

```
SMTP_HOST=smtp.your-provider.com
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...
MAIL_FROM=ReliefRoute <relief@your-domain>
```

New accounts then get a link to confirm their address, and *Forgot your password?* emails a reset link. Until then, a duty officer makes reset links in the Team tab. Most providers ask you to verify the sending domain first.

## Turning on presentation mode

For a demonstration with nobody in Andhra Pradesh: 42 simulated trucks and water depots, marked as simulated on every screen. Set `DEMO_FLEET=true` in `infra/deploy/.env`, then:

```bash
docker compose -f infra/deploy/docker-compose.prod.yml --env-file infra/deploy/.env --profile demo up -d --build
```

Anyone who can sign in sees the simulated fleet, so turn it off before real use. Stop `demo-fleet`, call `DELETE /api/demo` with the operator token, set `DEMO_FLEET=false` and run `up -d` without the profile.

## Updating

```bash
git pull
DC="docker compose -f infra/deploy/docker-compose.prod.yml --env-file infra/deploy/.env"
$DC up -d --build && $DC restart caddy
```

Migrations run by themselves first. Caddy needs the restart because it reads its config once, and `up` does not notice a changed Caddyfile.

## Trying it on one PC first

On Windows, this runs the same images at **http://localhost:3000**, with no domain needed:

```powershell
.\infra\dev\stack.ps1 prod        # stop it with: .\infra\dev\stack.ps1 prod-down
.\infra\dev\stack.ps1 demo        # the same, with simulated drivers; demo-off removes them
```

It keeps its own database and its own secrets and keys in `infra\deploy\local.env`. A browser accepts GPS from `localhost` without HTTPS, so the driver screen works there too.

## Hosting the web app on Vercel instead

Not needed, but possible. Set the project's root directory to `frontend`, and set `API_ORIGIN` to the API's address and `NEXT_PUBLIC_REGION_BBOX` to the region. Next then forwards API calls, so sign-in cookies keep working.
