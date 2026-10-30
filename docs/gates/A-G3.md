# Gate 3 report: Instance A (Brain), closed loop and robustness

Run on 2026-10-31 on a new Windows machine, on branch `integration/gate3`, against real PostGIS, Redis and Valhalla (Andhra Pradesh graph, 757 tiles).

## Test results

```
288 passed, 3 skipped, 4 warnings in 337.42s
SKIPPED [3] tests\brain\live_sources.py:37: SACHET polygon endpoint is rate-limiting this IP (HTTP 403)
audit_runtime.py: 0 finding(s)
driver-pwa queue tests: 6 passed
```

## What was built

- **Route Monitor** (`graph/monitor.py`): tracks stop progress from GPS, moves orders to `in_transit` and `delivered`, and detects off-route, stalled and offline drivers and hazards crossing the road ahead.
- **Replanner** (`graph/replan.py`): a child plan takes over one route. Goods at the supplier may go on any vehicle, goods on a truck stay on it, and goods on a lost truck are escalated with the last known position. Parent orders shrink by what moved.
- **VRP on-board load** (`solvers/vrp.py`): an `unload` stop models goods already on a truck, which regains that room once they are off.
- **Alternate hub:** proposed when nothing is routable, but only if goods can actually reach it.
- **Recovery:** checkpoint resume after a worker dies, requeue sweeps for events lost to a Redis outage, a retry sweep for deviations.
- **Chaos tests:** `tests/brain/chaos/test_chaos_brain.py`.

## Edge cases, with evidence

| Edge case | Result | Evidence |
|---|---|---|
| Driver leaves the route | Replanned in 5 s, no human needed | Live, below |
| Hazard crosses the road ahead | Detected in the same second | Live, below; route around it in `test_hazard_growing_across_the_road_ahead_reroutes_around_it` |
| Driver goes offline, goods at supplier | Reassigned to another vehicle | `test_offline_driver_load_is_reassigned_to_another_vehicle` |
| Driver goes offline, goods on the truck | Escalated with last known position | Live, below |
| No safe route | Flagged unreachable, alternate hub proposed if one helps | Live, below; `test_alternate_hub_*` |
| Supplier shortage | Split across suppliers, shortfall escalated | Live, below |
| Malformed LLM output | Repair loop, then labelled fallback | `test_llm_failure_uses_labelled_rules_and_still_needs_a_human` |
| Duplicate alert or dispatch | Nothing changes | `test_graph_pauses_for_approval_then_dispatches_idempotently`, `test_a_duplicate_event_for_a_healthy_plan_does_nothing` |
| Stale data | Fresh plan requested, or plan refused | `test_stock_taken_while_waiting_supersedes_the_plan_and_requests_a_fresh_one` |

## Live run: real data, labelled simulated vehicles

Two `TEST-SIM-GPS` vehicles, a drill over the Krishna at Vijayawada, real AePDS rice stock.

```
20:26:13 sentinel   Census population 468,972 ... 1 city/town figure(s) scaled by the zone's share of their mandal, 1,048,240 before scaling
20:26:16 procurement 6 orders across 3 supplier(s); 5 shortfall(s) escalated
20:26:22 route_planner Attempt 1: 2 route(s), 0 unserved order part(s), 40 part(s) deferred to the next wave
20:26:22 critic     Attempt 1: PASS (1 hazards checked, clearance >= 100 m, capacity and horizon OK)
20:26:22 supervisor Waiting for human approval: ... 269,660 kg exceeds 5,000 kg, drill, shortfalls or unserved orders, goods deferred to the next wave
20:26:49 human      APPROVED by Claude (labelled drill test): Gate 2 live verification
20:26:49 supervisor Re-checked before dispatch: hazards, routes, stock and vehicles still hold
20:26:49 dispatcher 6 order(s) and 2 route(s) committed
20:31:02 replanner  TEST-SIM-GPS-A reached pickup AP Civil Supplies PDS stock, G Konduru (Ntr) (1/10 stops)
20:31:19 replanner  Deviation on TEST-SIM-GPS-A: off route (9494 m from the route)
20:31:19 replanner  off route: 5 order(s) to re-route, 0 line(s) stranded on the vehicle
20:31:24 critic     Attempt 1: PASS
20:31:24 supervisor Auto-approved: re-route of goods already approved
20:31:24 dispatcher 3 order(s) and 1 route(s) committed
```

The simulator picked up the replacement route by itself. Stock reconciled exactly afterwards:

```
 kg_on_live_routes | kg_on_open_orders | kg_reserved
           22927.0 |           22927.0 |     22927.0
```

Hazard across the road, published through the drill API:

```
20:35:08 replanner Deviation on TEST-SIM-GPS-A: hazard on route (hazard e8a9a441-...)
20:35:09 replanner hazard on route: 2 order(s) to re-route, 0 line(s) stranded on the vehicle
20:35:14 route_planner Attempt 1: 0 route(s), 2 unserved order part(s)
```

The result was correct: this flood landed 580 m from the G Konduru pickup point, and the matrix showed no safe road into it from any vehicle. The goods were escalated. It also showed a flaw, now fixed: the hub proposed here could not have helped, because no truck could collect the goods.

Driver goes silent, with goods on board:

```
20:33:36 truck B's process stopped
20:39:03 route_deviations: offline
20:39:03 replanner offline: 0 order(s) to re-route, 1 line(s) stranded on the vehicle
20:39:03 Plan failed: nothing can be re-routed: the goods are on a vehicle that cannot move
         RICE_KG 5992: on a vehicle ... recover it at its last known position
```

## Bugs the live runs found

Every test passed before these appeared. None could be seen with fixtures.

1. **Forecast rain closed the roads.** Every active alert was an exclusion zone, so 3,000 km² rainfall cells for a date two days away left 0 of 40 suppliers reachable. Fixed: only road-closing hazards block routing (`routing/repository.py`).
2. **Relief points inside the router's own margin.** The nearest sites were 8 to 62 m from the zone, inside the 250 m exclusion, so nothing could reach them. Fixed: `relief_min_distance_m`.
3. **A truck with cargo counted as full for its whole route.** Goods it could collect after unloading were left unserved. Fixed in the VRP.
4. **A replan waited for a human.** Unserved goods blocked dispatch while a truck sat on the road. Fixed: it gets its route, and the rest is escalated.
5. **Zero-kilo orders left open.** A 0.0003 kg rounding residue. Fixed.
6. **Deadlines in UTC.** Fixed: drivers now see IST.

Reading the code also caught two the tests had missed: a truck loading at a stop looked stalled, and a phone dying before its first fix was never noticed.

## Self-audit

- (a) `audit_runtime.py`: 0 findings.
- (b) `check_ownership.py` is not meaningful here: one worker carried both roles on one branch, with commits scoped per owner.
- (c) No secrets tracked; `.env` files ignored.
- (d) Every external call has a timeout, retry and failure path; outages now reach the plan as plain reasons.
- (e) Every agent output is validated by Pydantic.

## Not verified

- **Gemini on this machine.** `GEMINI_API_KEY` is blank, so these runs used the labelled rule-based steps. Replans have never run with the live LLM.
- **Real phone GPS in Andhra Pradesh.** Vehicles were simulated, or the real Driver PWA with an emulated location.
- **Re-sourcing.** When a supplier is cut off after dispatch, the goods are escalated. Procurement is not re-run.
- **Two brain workers.** Crash recovery assumes one.
