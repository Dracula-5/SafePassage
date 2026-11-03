# Contract change 010: the driver confirms the route when GPS is lost

**Status: PENDING human review.** Requested by the project owner on 2026-11-03 ("if driver losses gps signal there will be a query message popped on driver screen for verification"). Implemented in owner code and migration 210. Nothing under `contracts/` changed except this file. The problem reports asked for in the same request are A's, in request 009.

## What changes

- **On the phone:** when it has signal but no GPS fix on a route for a minute, the driver screen asks "We can't see your GPS. Are you still driving your route?" It asks again every 3 minutes while the GPS stays lost. "No, I have a problem" opens the problem report from request 009.
- **New endpoint:** `POST /api/vehicles/{vehicle_id}/check-in`, with the same access as the assignment. It sets the new `vehicles.gps_checkin_at` (migration 210) and answers `{"vehicle_id", "checked_in_at"}`. It is not a position, and the truck still shows as offline.
- **Effect on reassignment:** the monitor (A, `graph/monitor.py`) holds off the `offline` deviation while the last check-in is younger than `offline_grace_s` (5 minutes). It writes once per answer to the activity log why it held off. With no answer, a silent truck's goods are reassigned as before.
- **Vehicle shape:** `Vehicle` gains `gps_checkin_at`. The duty officer's truck popup shows it while the truck is offline.

## Changes to apply to `openapi.yaml`

- The endpoint above.
- `Vehicle.gps_checkin_at`: `string (date-time) | null`.
