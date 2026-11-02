# Temporary free Render demo

This deploys only the Docker API, a free Render Postgres database, and a free Render Key Value instance. It connects to the existing Vercel frontend at <https://safepassage-frontend.vercel.app/>; it does not deploy or replace that frontend, and Netlify is not needed. It is not a full ReliefRoute deployment: ingestion and planning workers and the Valhalla router are omitted, so live data collection, simulated vehicles, and route planning will not run. The API can serve basic status, account, and stored-data requests once its database is initialized.

Render's free web service can sleep after inactivity, and its filesystem is ephemeral. Free Postgres expires after 30 days and has no backups; free Key Value data can be lost when it restarts. Render may restart free services at any time. This setup is for a temporary demo, not production. See [Render's free-instance limits](https://render.com/docs/free).

## Deploy

1. In Render, create a Blueprint from `Dracula-5/SafePassage`, branch `main`. Set the Blueprint file path to `render-free.yaml` (the root `render.yaml` describes the separate, paid full stack).
3. Review the plan before applying it. Every service in `render-free.yaml` uses the Free plan. Do not upgrade any service if zero cost is required.
4. Wait for the Postgres database, Key Value, and API services to finish provisioning. The API health check is `/health`; it should return `{"status":"ok","checks":{"postgres":"ok","redis":"ok"}}`.
5. In Vercel, open the existing project serving `safepassage-frontend.vercel.app` and add the Production environment variable `API_ORIGIN` with the API service's exact Render HTTPS origin, without a trailing slash (copy it from the Render service page). The frontend's Next.js rewrites use this server-side variable, so it must not be prefixed with `NEXT_PUBLIC_`.
6. Redeploy the Vercel project. Check `https://safepassage-frontend.vercel.app/api/status`; it should return JSON after the first Render wake-up. Render may take about a minute to wake a sleeping free web service.

Render generates `OPERATOR_TOKEN` and `ENROLMENT_CODE` in the environment group. Retrieve them from the Render dashboard when needed; never put them in the frontend or commit them.

The existing full deployment is in [render.yaml](../render.yaml). It adds paid workers, Valhalla, and larger database/compute plans.
