# Contract change 008: automatic approval by rules

**Status: PENDING human review.** Requested by the project owner on 2026-11-01 ("automate the approving process ... in any ambiguous case redirect to human"). Implemented in owner code. Nothing under `contracts/` changed except this file.

## What changes

The system design had a person approve almost every plan. A plan now goes out by itself when `agents/approval.py` finds nothing unclear. A person still decides when any of these holds:

- Extreme real alert
- Real alert with simulated trucks
- Hazard data out of date
- AI assessment unavailable, or the AI is unsure
- Unverified supplier
- Over 300 t moving at once (what leaves now, not everything allocated)
- No truck for over 20% of the goods
- A route under 200 m from a hazard
- The safety check rejected the first route
- A route crosses a wide warning area

Shortfalls no longer hold a plan back. The rules are plain code, so a model can add a person but never remove one. `AUTO_APPROVE=false` sends every plan to a person, and the limits are settings.

This replaces a promise in request 006: a drill using simulated trucks no longer waits for a person. A real alert using them still does.

## The next wave goes by itself

Added on 2026-11-01 at the owner's request, after a 355 t drill stopped on "Over 300 t" with under 300 t on its trucks. Nothing sent the goods held back for a next wave: every alert got one automatic plan and the rest was left.

When a wave's trucks finish and the alert is active, `graph/waves.py` starts a new plan for what is still needed. It takes the same steps and the same rules, so an unclear wave still stops for a person, and any plan for an alert now takes off what is already ordered for it, so nothing goes twice. A wave reuses the alert's assessment instead of asking the LLM again, unless that assessment was only a fallback. One that finds no stock or truck for now tries again after 5 minutes, because depots refill and trucks come back. Waves stop when the alert ends, when nothing is left, after `NEXT_WAVE_MAX` (10, and 0 turns them off), when a person rejects one, or when no truck has finished for 30 minutes. Goods on a lost truck still need a person, and so does a plan that goes out of date 3 times in an hour.

## Wide warnings are not closures

A real zone over 5,000 km2 no longer counts as a road closure. Tropical Cyclone FOUR-26 arrived on 2026-11-01 with GDACS zones of 107,000 and 30,000 km2. Treated as closures, they left "no safe relief point" and timed the router out, so every coastal plan failed. Trucks may now cross such a zone, but a person decides first. Drills are always closures.

## Changes to apply to `openapi.yaml`

- `PlanDetail` gains `needs_person`: why a person must decide, and `waves_left`: how many more waves can follow, 0 once the alert has ended.
- `PlanSummary` gains `next_wave`: a plan the rules started.

## Who is affected

- **`approval_weight_kg` is removed** in favour of `auto_approve_max_kg`.
- **The plan brief is shorter:** two sentences at most, written from totals.
