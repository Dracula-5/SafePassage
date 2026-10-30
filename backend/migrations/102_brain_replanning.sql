-- Owner: A. Closed-loop replanning: stop progress on routes, durable deviation records, and the
-- plan view B's SSE stream reads (contract request A-plan-and-admin-views).
ALTER TABLE routes ADD COLUMN completed_stops integer NOT NULL DEFAULT 0 CHECK (completed_stops >= 0);
ALTER TABLE routes ADD COLUMN started_at timestamptz;
ALTER TABLE routes ADD COLUMN completed_at timestamptz;
ALTER TABLE routes ADD COLUMN parts jsonb NOT NULL DEFAULT '[]';   -- what the vehicle carries, per order part

CREATE TABLE route_deviations (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    route_id                uuid NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
    plan_id                 uuid NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    vehicle_id              uuid NOT NULL REFERENCES vehicles(id),
    kind                    deviation_kind NOT NULL,
    hazard_id               uuid REFERENCES hazards(id),
    distance_from_route_m   double precision,
    last_position           geometry(Point, 4326),
    detected_at             timestamptz NOT NULL DEFAULT now(),
    resolved_at             timestamptz,
    replan_id               uuid REFERENCES plans(id)
);
-- One open deviation per route and kind: a noisy GPS trace cannot start a second replan.
CREATE UNIQUE INDEX route_deviations_open_idx ON route_deviations (route_id, kind) WHERE resolved_at IS NULL;

CREATE VIEW v_plan_status AS
SELECT id, parent_plan_id, thread_id, status, trigger_kind, hazard_ids, is_drill, requires_approval, summary,
       created_at, updated_at
FROM plans;
