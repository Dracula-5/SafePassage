# Contract change 002: two read-only views for cross-owner reads

**Status: PENDING human review.** Implemented as additive owner migrations so nothing under
`contracts/db/` or `contracts/openapi.yaml` changes. Both views can be dropped without touching a
table.

- `v_admin_areas` (B migration 203): A's Needs agent reads mandal boundaries to scale a city's
  census figure by how much of its mandal the hazard covers. Without it, a drill clipping
  Vijayawada counted all 1,048,240 residents.
- `v_plan_status` (A migration 102): B's `/api/stream` emits the `plan` SSE event that
  `openapi.yaml` already documents. It was not emitted because plans had no contract view.

Neither view can live in `contracts/db/patches/`: patches run before owner migrations, so
`admin_areas` (202) would not exist yet.
