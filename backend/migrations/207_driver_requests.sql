-- A duty officer asks one driver to go to one place (telemetry/requests.py). Every truck also gets a short
-- permanent number, so a person can find it on the map and call it by name.
ALTER TABLE vehicles ADD COLUMN fleet_number integer GENERATED ALWAYS AS IDENTITY;

CREATE TABLE driver_requests (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    vehicle_id    uuid NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    hazard_id     uuid NOT NULL REFERENCES hazards(id) ON DELETE CASCADE,
    requested_by  text NOT NULL,
    status        text NOT NULL DEFAULT 'sent' CHECK (status IN ('sent', 'accepted', 'declined', 'withdrawn')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    answered_at   timestamptz
);
CREATE UNIQUE INDEX driver_requests_one_waiting_idx ON driver_requests (vehicle_id) WHERE status = 'sent';
CREATE INDEX driver_requests_place_idx ON driver_requests (hazard_id, status);
