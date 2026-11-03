# Contract change 009: a driver reports a problem on the road

**Status: PENDING human review.** Requested by the project owner on 2026-11-03 ("from driver screen keep some options like traffic problem vehicle breakdown like that so that reroute can be started"). Implemented in owner code and migration 104. Nothing under `contracts/` changed except this file. The GPS check-in asked for in the same request is B's, in request 010.

## What changes

`POST /api/vehicles/{vehicle_id}/problem`, with the same access as the vehicle's assignment (its phone's device token, its signed-in driver, or a duty officer). Body: `{"problem": "traffic" | "road_blocked" | "breakdown" | "accident", "note"?: string ≤ 200}`. It answers `202 {"status": "rerouting" | "already_rerouting", "kind"}`, or `409` when the truck has no route.

A report opens a `route_deviations` row exactly as the monitor does, so the existing re-route follows, within the existing per-hour cap. No deviation kind is added: the frozen `deviation_kind` enum and `DeviationKind` are unchanged.

| Report | Deviation | What the re-route does |
| --- | --- | --- |
| `breakdown`, `accident` | `stalled` | The truck is lost: goods still at suppliers go to another truck, and goods on board are escalated for recovery. |
| `traffic`, `road_blocked` | `off_route` | The same truck keeps its deliveries. The new route avoids a 150 m circle 1.5 km ahead on the old route, which is far enough that the truck stays outside it even at the widest exclusion margin. |

Migration 104 adds `route_deviations.reported_problem` and `reported_note`. The avoid zone steers only that one re-route: it is not a hazard, and the Critic does not check routes against it.

## Changes to apply to `openapi.yaml`

- The endpoint above, with its `ProblemReport` body.
