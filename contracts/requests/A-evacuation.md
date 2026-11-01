# Contract change 007: evacuations and colour-coded routes

**Status: PENDING human review.** Requested by the project owner on 2026-11-01 ("supply routes ... orange and escape or relief taking passenger routes as green ... trigger a evacuation request ... make sure the path avoids all drill and affected zones"). Implemented in owner code and migration 103, building on request 006. Nothing under `contracts/` changed except this file, so `openapi.yaml` does not describe these until this is approved and applied.

## What it does

A truck collects people just outside a zone and takes them to a safe point. There is nothing for the agents to reason about, so `graph/evacuation.py` runs beside the plan graph and writes an ordinary dispatched plan and route. The Route Monitor and the driver's screen then work unchanged.

A truck can't leave a closed zone by a road that avoids it, so people gather at a real facility at the edge. The way out avoids **every** active zone near it, forecasts included, which is stricter than supply routes, and Shapely checks it before any truck gets it. A zone holding the pickup or safe point can't be avoided. If forecast cells leave no way round, it may cross those, but never a drill or a closure, and what it crossed is recorded and shown. A new zone across the way out brings a new route with no human involved.

Presentation mode asks for one every 10 to 15 minutes while a drill or severe alert is active, with a made-up number of people, marked simulated. A duty officer can also request one from a zone on the map.

## Decision this needs

The approval gate does not apply: the request itself sends the nearest free truck. A person pressed the button, or the system is in presentation mode, where every truck is simulated. People waiting in a zone should not wait for a second click. If you want a second approval step for real evacuations, say so.

## Changes to apply to `openapi.yaml`

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /api/evacuations` | Operator | `{hazard_id, people, simulated}`. Finds a real settlement in the zone; `422` if there is none |
| `GET /api/evacuations` | Operator | Requests from the last day, with their stage |
| `GET /api/routes/live` | Operator | Every route on the road now, simplified for the map |

**Changed.**

- `Route` gains `kind`: `supply` or `evacuation`.
- `PlanSummary` gains `evacuation`.
- `GET /api/overview` gains `people_safe` and `people_waiting`.

## Who is affected

- **Database:** migration 103 adds `routes.kind` and a new table `evacuations`.
- **B:** `DELETE /api/demo` also removes simulated evacuations, and the simulator asks for them through the public API.
