-- Simulated drivers for presentations (telemetry/demo.py). Stays empty unless DEMO_FLEET is true.
-- The device token is stored in the clear because the simulator has to use it, which is safe only
-- because these vehicles are simulated. Real vehicles keep only a hash.
CREATE TABLE demo_drivers (
    vehicle_id    uuid PRIMARY KEY REFERENCES vehicles(id) ON DELETE CASCADE,
    device_token  text NOT NULL,
    town          text NOT NULL,
    lon           double precision NOT NULL,
    lat           double precision NOT NULL,
    mode          text NOT NULL DEFAULT 'normal' CHECK (mode IN ('normal', 'gps_lost', 'off_route')),
    created_at    timestamptz NOT NULL DEFAULT now()
);
