# Contract change 006: presentation mode with simulated drivers

**Status: PENDING human review.** Requested by the project owner on 2026-11-01 ("simulate drivers with andhra pradesh driver names and number plates ... at least 10 drivers ... keep them active"). Implemented in owner code and migration 206, building on requests 003 and 004. Nothing under `contracts/` changed except this file, so `openapi.yaml` does not describe these until this is approved and applied.

## Why

Nobody on the team can show a drill without trucks in Andhra Pradesh: the road graph stops at the state's border, and a phone outside it can't be routed. A real drill failed on 2026-11-01 for this reason, with "no online vehicle".

## The exception this asks for

The master prompt forbids fake data at runtime, and this adds simulated drivers to a running system, so it needs an explicit decision. These limits apply:

- All of it is off unless `DEMO_FLEET=true`, and the simulator only runs under compose profile `demo`. It stays in `backend/tests/tools/` and is not in the production image.
- Every vehicle's label starts with `TEST-SIM-GPS`, every depot's with `TEST-SIM`. Screens show a SIMULATED badge and a banner while the mode is on.
- A plan that uses a simulated vehicle always waits for a person, even for a real alert.
- Simulated drivers have no phone number, so no message can reach anyone.
- `DELETE /api/demo` removes them and their stock before going live.

## Changes to apply to `openapi.yaml`

**New endpoints.**

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /api/demo` | Public | Whether presentation mode is on, how many trucks, and the towns |
| `GET /api/demo/fleet` | Operator | The simulated trucks and their device tokens, for the simulator |
| `POST /api/demo/drivers` | Operator | Adds a simulated driver |
| `POST /api/demo/drivers/{id}/mode` | Operator | `normal`, `gps_lost` or `off_route`, to show the closed loop |
| `POST /api/demo/my-truck` | Driver | Turns the signed-in driver's own truck into a simulated one |
| `DELETE /api/demo` | Operator | Ends presentation mode |
| `GET /api/supplier/orders` | Supplier | What dispatched plans asked this supplier to prepare. Not specific to presentations |
| `GET /api/suppliers` | Operator | Every supplier and its stock, for the Directory page. Not specific to presentations |
| `POST`, `GET /api/driver-requests`, `DELETE /api/driver-requests/{id}` | Operator | Ask one driver to go to one drill or alert, see the answers, or withdraw a request |
| `GET /api/hazards/{id}/distances` | Operator | How far each truck is from a place, nearest first |
| `GET /api/vehicles/{id}/request`, `POST /api/vehicles/{id}/request/answer` | That vehicle's driver, phone or an operator | The request waiting for this driver, and their answer |

**Changed.**

- `Vehicle` gains `simulated`, `on_route` and `fleet_number`, a short permanent number shown on the map.
- A plan uses drivers who accepted a request for that place before any others.
- `GET /api/vehicles/{id}/assignment` also accepts that vehicle's signed-in driver.
- `POST /api/driver/vehicle/device` answers `409` for a simulated truck, which has no phone.

## Changes that are not in the contract

- The Route Planner offers the solver the nearest `PLANNER_MAX_VEHICLES` vehicles (12), and fewer when the sites leave less room under Valhalla's 2,500 matrix pairs. Without this, a large fleet near a disaster would fail as a false "routing service unavailable". It applies to real fleets too.
- Orders are cut to fit most of the fleet, not the largest truck: the smallest vehicle at least half the largest's size sets the part size. It applies to real fleets too.
- A truck with a real person behind it is used before a fully simulated one: the planner lists it first, and the solver pays a 6-hour cost to start a simulated truck, far below what it pays to leave goods behind. The Route Planner reads `vehicles.account_id` for this, which `v_vehicle_status` does not expose.
- An order, stop or message for a drill with no phone number is now a plain step on the timeline, not an error.

## Who is affected

- **Database:** new table `demo_drivers` in migration 206. It holds simulated trucks' device tokens in the clear, because the simulator has to use them.
- **Database:** migration 207 adds a new table `driver_requests` and a `vehicles.fleet_number` column. Migration 208 takes town coordinates back out of `demo_drivers`, because `contracts/tools/audit_runtime.py` rightly refuses them in runtime code: they live in the simulator.
- **A:** `graph/nodes.py` reads `SIMULATED_LABEL` from `core/config.py` for the approval rule.
- **`contracts/ownership.txt`:** `backend/tests/tools/` still has no owner.
