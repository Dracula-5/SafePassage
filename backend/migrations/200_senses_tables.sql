-- Owner: Instance B (Senses & Face). Written at Gate 0 as the contract baseline;
-- B may ADD columns/indexes in later 2xx migrations, never drop/rename contract columns.

CREATE TABLE hazards (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source          hazard_source NOT NULL,
    source_ref      text NOT NULL,                         -- upstream id (CAP identifier, GDACS eventid, ...)
    dedupe_key      text NOT NULL UNIQUE,                  -- normalized key collapsing duplicate alerts
    hazard_type     hazard_type NOT NULL,
    severity        hazard_severity NOT NULL,
    status          hazard_status NOT NULL DEFAULT 'active',
    headline        text NOT NULL,
    description     text,
    area_desc       text,                                  -- e.g. CAP areaDesc / district names
    geom            geometry(MultiPolygon, 4326) NOT NULL,
    effective_at    timestamptz,
    expires_at      timestamptz,
    issued_at       timestamptz,
    fetched_at      timestamptz NOT NULL,
    version         integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    raw             jsonb NOT NULL DEFAULT '{}',           -- upstream payload, for audit
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (ST_IsValid(geom)),
    CHECK ((source = 'drill') = (source_ref LIKE 'drill:%'))
);
CREATE INDEX hazards_geom_idx ON hazards USING gist (geom);
CREATE INDEX hazards_active_idx ON hazards (status, expires_at);

CREATE TABLE facilities (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source      facility_source NOT NULL,
    source_ref  text NOT NULL,                             -- e.g. osm node/123, datagov record id
    kind        facility_kind NOT NULL,
    name        text NOT NULL,
    geom        geometry(Point, 4326) NOT NULL,
    capacity_people integer CHECK (capacity_people >= 0), -- shelters, if known from source
    attributes  jsonb NOT NULL DEFAULT '{}',
    fetched_at  timestamptz NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, source_ref)
);
CREATE INDEX facilities_geom_idx ON facilities USING gist (geom);

CREATE TABLE suppliers (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    facility_id         uuid NOT NULL REFERENCES facilities(id),
    name                text NOT NULL,
    contact_phone       text,
    contact_whatsapp    text,
    preferred_language  text NOT NULL DEFAULT 'en',
    portal_token_hash   text,                              -- never store raw tokens
    verified            boolean NOT NULL DEFAULT false,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- Current stock snapshot, one row per supplier x item, replaced on each report.
CREATE TABLE stock (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id     uuid NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    item_code       item_code NOT NULL,
    quantity        double precision NOT NULL CHECK (quantity >= 0),
    unit_weight_kg  double precision NOT NULL CHECK (unit_weight_kg > 0),   -- declared by the supplier
    reported_at     timestamptz NOT NULL,
    reported_via    stock_report_channel NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (supplier_id, item_code)
);

CREATE TABLE vehicles (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    label               text NOT NULL,
    driver_name         text,
    driver_phone        text,
    driver_language     text NOT NULL DEFAULT 'hi',
    capacity_kg         double precision NOT NULL CHECK (capacity_kg > 0),
    home_facility_id    uuid REFERENCES facilities(id),
    status              vehicle_status NOT NULL DEFAULT 'available',
    last_position       geometry(Point, 4326),
    last_seen_at        timestamptz,
    device_token_hash   text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE telemetry (
    id          bigserial PRIMARY KEY,
    vehicle_id  uuid NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    geom        geometry(Point, 4326) NOT NULL,
    speed_mps   double precision CHECK (speed_mps >= 0),
    heading_deg double precision CHECK (heading_deg >= 0 AND heading_deg < 360),
    accuracy_m  double precision CHECK (accuracy_m >= 0),
    recorded_at timestamptz NOT NULL,                      -- device clock
    received_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (vehicle_id, recorded_at)                       -- PWA offline buffer may resend
);
CREATE INDEX telemetry_vehicle_time_idx ON telemetry (vehicle_id, recorded_at DESC);

-- Health of each live source, for circuit breakers and "data stale" badges.
CREATE TABLE source_health (
    source              text PRIMARY KEY,                  -- e.g. 'sachet', 'open_meteo', 'overpass', 'valhalla'
    breaker_state       text NOT NULL CHECK (breaker_state IN ('closed', 'open', 'half_open')),
    last_success_at     timestamptz,
    last_failure_at     timestamptz,
    last_error          text,
    ttl_seconds         integer NOT NULL CHECK (ttl_seconds > 0),
    fallback_source     text,
    updated_at          timestamptz NOT NULL DEFAULT now()
);
