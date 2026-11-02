# Deploying the frontend on Netlify and backend on Render

This deploys the Next.js frontend to Netlify and the API, workers, database, Redis, and Valhalla router to Render. It is the full hosted stack described by [`render.yaml`](../render.yaml) and [`netlify.toml`](../netlify.toml); it is not the temporary free demo in [`deploy-render-free.md`](deploy-render-free.md).

## Before deploying

- Connect the GitHub repository to both Render and Netlify.
- Deploy the frontend as a Netlify site named `reliefroute-ap`, or change `PUBLIC_URL` in `render.yaml` to the site's HTTPS URL.
- Deploy the `integration/gate3` branch, which is the branch selected in `render.yaml`.
- Review the paid Render service plans and current prices before applying the Blueprint. The database, workers, Redis, and Valhalla are continuously running services; this is not a free-tier deployment. Docker builds download and verify the current Geofabrik extract, so the first backend/router builds take longer and need additional build disk.

## Deploy the backend

1. In Render, create a Blueprint from this repository and select the `render.yaml` Blueprint file.
2. Review the services, regions, and paid plans, then apply the Blueprint.
3. In the Render dashboard, set `GEMINI_API_KEY` on `reliefroute-brain` if Gemini-powered triage and driver instructions are required. Set `FIRMS_MAP_KEY` on `reliefroute-ingestion` if fire alerts are required. Configure optional mail or messaging credentials only when those integrations are enabled.
4. Wait for the API, database, Redis, workers, and Valhalla service to finish deploying. The router downloads the verified road extract in its image build, then builds its graph on first startup; allow several minutes before testing routes. The generated `OPERATOR_TOKEN` and `ENROLMENT_CODE` are in the `reliefroute-shared` environment group. Keep them out of the frontend and source control.
5. Confirm the API's `/health` endpoint reports both Postgres and Redis as healthy. The worker logs should show successful startup; without a Gemini key, LLM-backed behavior may use its configured fallback.

## Deploy the frontend

1. Create a Netlify site connected to the same repository and use the repository's `netlify.toml`. It sets the base directory to `frontend`, builds with Node 22, and configures the API proxy redirects.
2. The default redirects expect the Render API at `https://reliefroute-api.onrender.com` and the site at `https://reliefroute-ap.netlify.app`. If Render assigns a different API URL or you use a custom domain, update the matching API destinations in `netlify.toml` and `PUBLIC_URL` in `render.yaml`, then redeploy both sides.
3. Trigger a Netlify deploy after the Render API is available. The redirects keep API calls on the frontend's origin, so browser sessions and cookies work without exposing backend credentials to client code. No `NEXT_PUBLIC_API` secret or direct browser-to-API CORS access is needed.

## Verify the deployment

- Open the Netlify site and check `/health`; it should return `{"status":"ok","checks":{"postgres":"ok","redis":"ok"}}`.
- Check `/api/status` through the Netlify site to verify the API proxy.
- Sign up as an operator with `OPERATOR_TOKEN`, then check the operator dashboard. Give drivers `ENROLMENT_CODE` only through a trusted channel.
- Check Render logs for the ingestion and planning workers. Verify routing and map tiles in the UI before relying on the service for live operations.

For local Docker deployment on one VM, see [`deploy.md`](deploy.md). For the limited, temporary Render free-tier API demo with the existing Vercel frontend, see [`deploy-render-free.md`](deploy-render-free.md).
