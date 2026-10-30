# Event catalog (Redis Streams) — FROZEN after Gate 0

Source of truth: `python/reliefroute_contracts/events.py`. `events.schema.json` is generated
from it and a test fails if they drift.

## Wire format
- Stream key = event type (e.g. `hazard.updated`).
- Each entry has one field, `data`, holding JSON of the envelope:
  ```
  { "event_id": uuid, "event_type": str, "schema_version": 1, "emitted_at": RFC3339,
    "producer": "brain"|"senses", "dedupe_key": str|null, "payload": {...} }
  ```
- `XADD <stream> MAXLEN ~ 50000 * data <json>` (use `app.core.bus.publish`).
- Consumer groups: `brain` (A) and `senses` (B). Read with `XREADGROUP`, `XACK` only after
  the side effects are committed. Unacked entries are re-claimed after 60 s.
- Consumers are idempotent on `event_id`; producers set `dedupe_key` for semantic duplicates.
- Events are notifications. Geometry and full rows are read from Postgres.

## Events

| Stream | Producer | Payload | Main consumer | Dedupe key |
|---|---|---|---|---|
| `hazard.updated` | B | `HazardUpdated` | A Sentinel / Replanner | `<hazard_id>:<version>` |
| `vehicle.telemetry` | B | `VehicleTelemetry` | A Replanner (deviation check) | `<vehicle_id>:<recorded_at>` |
| `supplier.stock.updated` | B | `SupplierStockUpdated` | A Procurement (re-check open plans) | `<supplier_id>:<item_code>:<reported_at>` |
| `plan.requested` | A | `PlanRequested` | A graph runner | `<plan_id>` |
| `plan.approved` | A | `PlanApproved` | A Dispatcher; B UI may display | `<plan_id>` |
| `vehicle.deviation` | A | `VehicleDeviation` | A Replanner; B PWA/UI may display | `<route_id>:<kind>:<detected_at>` |

### HazardUpdated
`hazard_id`, `change` (created|updated|expired|cancelled), `source`, `hazard_type`,
`severity`, `version` (row version after the change), `bbox` [min_lon,min_lat,max_lon,max_lat],
`area_km2`, `is_drill`.

### VehicleTelemetry
`telemetry_id` (telemetry.id), `vehicle_id`, `position` {lon, lat}, `recorded_at` (device),
`speed_mps`, `heading_deg`, `accuracy_m`.
B emits one event per stored fix (after dedupe), in recorded_at order.

### SupplierStockUpdated
`supplier_id`, `item_code`, `quantity`, `previous_quantity`, `reported_at`, `reported_via`.

### PlanRequested
`plan_id`, `trigger_kind`, `trigger_ref`, `hazard_ids`, `parent_plan_id` (replans), `requested_by`.

### PlanApproved
`plan_id`, `approval_id`, `decided_by`, `decided_at`.

### VehicleDeviation
`vehicle_id`, `plan_id`, `route_id`, `kind` (off_route|stalled|offline|hazard_on_route),
`detected_at`, `last_position`, `distance_from_route_m`, `hazard_id`.
