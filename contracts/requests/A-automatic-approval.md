# Contract change 008: automatic approval by rules

**Status: PENDING human review.** Requested by the project owner on 2026-11-01 ("automate the approving process ... in any ambiguous case redirect to human"). Implemented in owner code. Nothing under `contracts/` changed except this file.

## What changes

The system design had a person approve almost every plan. A plan now goes out by itself when `agents/approval.py` finds nothing unclear. A person still decides when any of these holds:

- Extreme real alert
- Real alert with simulated trucks
- Hazard data out of date
- AI assessment unavailable, or the AI is unsure
- Unverified supplier
- Over 300 t
- No truck for over 20% of the goods
- A route under 200 m from a hazard
- The safety check rejected the first route
- A route crosses a wide warning area

Shortfalls no longer hold a plan back. They stay on it for a person to chase. The rules are plain code, so a model can add a person but never remove one. `AUTO_APPROVE=false` sends every plan to a person, and the limits are settings.

This replaces a promise in request 006: a drill using simulated trucks no longer waits for a person. A real alert using them still does.

## Wide warnings are not closures

A real zone over 5,000 km2 no longer counts as a road closure. Tropical Cyclone FOUR-26 arrived on 2026-11-01 with GDACS zones of 107,000 and 30,000 km2. Treated as closures, they left "no safe relief point" and timed the router out, so every coastal plan failed. Trucks may now cross such a zone, but a person decides first. Drills are always closures.

## Changes to apply to `openapi.yaml`

- `PlanDetail` gains `needs_person`: why a person must decide.

## Who is affected

- **`approval_weight_kg` is removed** in favour of `auto_approve_max_kg`.
- **The plan brief is shorter:** two sentences at most, written from totals.
