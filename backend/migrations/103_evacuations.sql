-- Owner: A. Evacuations: a truck collects people just outside a zone and takes them to a safe point.
-- They need no agents, but they are ordinary plans and routes, so the monitor and the driver's screen work
-- as they do for goods. `kind` tells the two apart on the map.
ALTER TABLE routes ADD COLUMN kind text NOT NULL DEFAULT 'supply' CHECK (kind IN ('supply', 'evacuation'));

CREATE TABLE evacuations (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    hazard_id      uuid NOT NULL REFERENCES hazards(id) ON DELETE CASCADE,
    settlement     text NOT NULL,                          -- a real place inside the zone
    people         integer NOT NULL CHECK (people > 0),
    simulated      boolean NOT NULL DEFAULT false,         -- presentation mode made this request up
    requested_by   text NOT NULL,
    status         text NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting', 'assigned', 'failed')),
    failure        text,
    plan_id        uuid REFERENCES plans(id) ON DELETE SET NULL,
    route_id       uuid REFERENCES routes(id) ON DELETE SET NULL,
    pickup_name    text,
    safe_name      text,
    zones_avoided  integer,                                -- active zones near the way out that it stays clear of
    zones_crossed  integer,                                -- active zones it could not get round; always 0 for drills
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX evacuations_waiting_idx ON evacuations (created_at) WHERE status = 'waiting';
CREATE INDEX evacuations_route_idx ON evacuations (route_id);
