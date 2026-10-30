-- ReliefRoute database contract. FROZEN after Gate 0.
-- Applied once as the baseline by app.core.migrate; afterwards each owner adds its own
-- additive migrations (A: backend/migrations/1xx_*.sql, B: 2xx_*.sql). See db/SCHEMA.md.
-- Cross-owner reads go through the v_* views at the end; nobody writes the other side's tables.
-- Enum values must match contracts/python/reliefroute_contracts/enums.py (tested).
-- LangGraph checkpoint tables are created by langgraph-checkpoint-postgres setup(), not here.

-- ============================================================ extensions and enum types
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

CREATE TYPE hazard_source AS ENUM ('sachet', 'imd', 'open_meteo', 'gdacs', 'firms', 'cwc', 'drill');
CREATE TYPE hazard_type AS ENUM ('flood', 'heavy_rain', 'cyclone', 'storm', 'landslide', 'wildfire', 'heatwave', 'earthquake', 'tsunami', 'other');
CREATE TYPE hazard_severity AS ENUM ('minor', 'moderate', 'severe', 'extreme', 'unknown');
CREATE TYPE hazard_status AS ENUM ('active', 'expired', 'cancelled');
CREATE TYPE facility_kind AS ENUM ('hospital', 'shelter', 'warehouse', 'godown', 'school', 'fuel', 'depot', 'other');
CREATE TYPE facility_source AS ENUM ('osm', 'datagov', 'portal');
CREATE TYPE item_code AS ENUM ('WATER_L', 'RICE_KG', 'PULSES_KG', 'RTE_MEAL', 'TARPAULIN', 'BLANKET', 'HYGIENE_KIT', 'MEDICAL_KIT', 'ORS_SACHET');
CREATE TYPE stock_report_channel AS ENUM ('portal', 'whatsapp', 'api');
CREATE TYPE vehicle_status AS ENUM ('available', 'assigned', 'en_route', 'maintenance');
CREATE TYPE plan_trigger_kind AS ENUM ('hazard', 'manual', 'replan', 'drill');
CREATE TYPE plan_status AS ENUM ('pending', 'planning', 'awaiting_approval', 'approved', 'rejected', 'dispatched', 'superseded', 'unreachable', 'failed');
CREATE TYPE route_status AS ENUM ('planned', 'active', 'completed', 'abandoned');
CREATE TYPE order_status AS ENUM ('draft', 'approved', 'dispatched', 'in_transit', 'delivered', 'cancelled');
CREATE TYPE approval_decision AS ENUM ('approved', 'rejected');
CREATE TYPE agent_node AS ENUM ('supervisor', 'sentinel', 'needs', 'procurement', 'route_planner', 'critic', 'dispatcher', 'replanner', 'human');
CREATE TYPE agent_event_type AS ENUM ('node_started', 'node_finished', 'tool_call', 'tool_result', 'decision', 'critic_rejection', 'approval_requested', 'approval_decided', 'dispatch', 'data_stale', 'error');
CREATE TYPE deviation_kind AS ENUM ('off_route', 'stalled', 'offline', 'hazard_on_route');

-- ============================================================ B-owned tables (Senses)
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

-- ============================================================ A-owned tables (Brain)
CREATE TABLE plans (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_plan_id      uuid REFERENCES plans(id),
    thread_id           text NOT NULL UNIQUE,              -- LangGraph thread id
    trigger_kind        plan_trigger_kind NOT NULL,
    trigger_ref         text,
    hazard_ids          uuid[] NOT NULL DEFAULT '{}',
    status              plan_status NOT NULL DEFAULT 'pending',
    is_drill            boolean NOT NULL DEFAULT false,
    requires_approval   boolean NOT NULL DEFAULT true,
    critic_attempts     integer NOT NULL DEFAULT 0 CHECK (critic_attempts >= 0),
    summary             text,                              -- LLM explanation for humans
    demand              jsonb,                             -- validated Needs output
    allocation          jsonb,                             -- validated Procurement output
    unreachable_reason  text,
    requested_by        text NOT NULL,
    idempotency_key     text UNIQUE,                       -- from Idempotency-Key header
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX plans_status_idx ON plans (status, created_at DESC);

CREATE TABLE routes (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id             uuid NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    vehicle_id          uuid NOT NULL REFERENCES vehicles(id),
    status              route_status NOT NULL DEFAULT 'planned',
    geom                geometry(LineString, 4326) NOT NULL,
    distance_m          double precision NOT NULL CHECK (distance_m >= 0),
    duration_s          double precision NOT NULL CHECK (duration_s >= 0),
    min_hazard_clearance_m double precision,               -- verified by the Critic with Shapely
    stops               jsonb NOT NULL,                    -- ordered [{facility_id, kind, eta, order_ids}]
    load_kg             double precision NOT NULL CHECK (load_kg >= 0),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX routes_vehicle_idx ON routes (vehicle_id, status);
CREATE INDEX routes_geom_idx ON routes USING gist (geom);

CREATE TABLE orders (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id                 uuid NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    route_id                uuid REFERENCES routes(id),
    supplier_id             uuid NOT NULL REFERENCES suppliers(id),
    destination_facility_id uuid NOT NULL REFERENCES facilities(id),
    status                  order_status NOT NULL DEFAULT 'draft',
    idempotency_key         text NOT NULL UNIQUE,
    dispatched_at           timestamptz,
    delivered_at            timestamptz,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX orders_plan_idx ON orders (plan_id);
CREATE INDEX orders_supplier_status_idx ON orders (supplier_id, status);

CREATE TABLE order_lines (
    order_id    uuid NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    item_code   item_code NOT NULL,
    quantity    double precision NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (order_id, item_code)
);

CREATE TABLE approvals (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id     uuid NOT NULL UNIQUE REFERENCES plans(id) ON DELETE CASCADE,
    decision    approval_decision NOT NULL,
    decided_by  text NOT NULL,
    reason      text,
    decided_at  timestamptz NOT NULL DEFAULT now()
);

-- Outbound driver/supplier messages; idempotency_key makes re-sends no-ops.
CREATE TABLE notifications (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id        uuid REFERENCES orders(id) ON DELETE SET NULL,
    channel         text NOT NULL CHECK (channel IN ('sms', 'whatsapp', 'voice')),
    recipient       text NOT NULL,
    language        text NOT NULL,
    body            text NOT NULL,
    idempotency_key text NOT NULL UNIQUE,
    provider_ref    text,                                  -- e.g. Twilio message SID
    status          text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'sent', 'delivered', 'failed')),
    error           text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Audit log and reasoning timeline. B's /api/stream tails this table by id.
CREATE TABLE agent_events (
    id          bigserial PRIMARY KEY,
    plan_id     uuid REFERENCES plans(id) ON DELETE CASCADE,
    thread_id   text,
    node        agent_node NOT NULL,
    event_type  agent_event_type NOT NULL,
    message     text NOT NULL,                             -- one-line, human readable
    payload     jsonb NOT NULL DEFAULT '{}',               -- tool inputs/outputs, violations
    source_ids  text[] NOT NULL DEFAULT '{}',              -- e.g. hazard:<uuid>, stock:<uuid>, sachet:<identifier>
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX agent_events_plan_idx ON agent_events (plan_id, id);

-- ============================================================ contract views (cross-owner reads)
-- Hazards that are currently in force (drills included; filter on source if needed).
CREATE VIEW v_active_hazards AS
SELECT h.*,
       (h.source = 'drill') AS is_drill
FROM hazards h
WHERE h.status = 'active'
  AND (h.expires_at IS NULL OR h.expires_at > now());

-- Vehicle with derived liveness. Offline threshold = VEHICLE_OFFLINE_AFTER_SECONDS (120).
CREATE VIEW v_vehicle_status AS
SELECT v.id, v.label, v.driver_name, v.driver_phone, v.driver_language, v.capacity_kg,
       v.home_facility_id, v.status, v.last_position, v.last_seen_at,
       EXTRACT(EPOCH FROM (now() - v.last_seen_at))::double precision AS seconds_since_seen,
       (v.last_seen_at IS NULL OR now() - v.last_seen_at > interval '120 seconds') AS is_offline
FROM vehicles v;

-- Each vehicle's current assignment: newest planned/active route of an approved/dispatched plan.
CREATE VIEW v_vehicle_assignment AS
SELECT DISTINCT ON (r.vehicle_id)
       r.vehicle_id, r.id AS route_id, r.plan_id, r.status AS route_status, p.status AS plan_status,
       p.is_drill, r.geom, r.stops, r.distance_m, r.duration_s, r.load_kg, r.created_at
FROM routes r
JOIN plans p ON p.id = r.plan_id
WHERE r.status IN ('planned', 'active')
  AND p.status IN ('approved', 'dispatched')
ORDER BY r.vehicle_id, r.created_at DESC;

-- Stock minus what open orders have already claimed. A delivered order still counts as
-- reserved until the supplier files a newer stock report (prevents double allocation).
CREATE VIEW v_available_stock AS
SELECT s.id AS stock_id, s.supplier_id, sp.facility_id, s.item_code, s.quantity,
       COALESCE(res.reserved, 0) AS reserved,
       GREATEST(s.quantity - COALESCE(res.reserved, 0), 0) AS available,
       s.unit_weight_kg, s.reported_at, s.reported_via
FROM stock s
JOIN suppliers sp ON sp.id = s.supplier_id
LEFT JOIN LATERAL (
    SELECT SUM(ol.quantity) AS reserved
    FROM orders o
    JOIN order_lines ol ON ol.order_id = o.id
    WHERE o.supplier_id = s.supplier_id
      AND ol.item_code = s.item_code
      AND (o.status IN ('approved', 'dispatched', 'in_transit')
           OR (o.status = 'delivered' AND o.delivered_at > s.reported_at))
) res ON true;

-- Source freshness for "data stale" badges and confidence-aware degradation.
CREATE VIEW v_source_freshness AS
SELECT sh.*,
       (sh.last_success_at IS NULL OR now() - sh.last_success_at > make_interval(secs => sh.ttl_seconds)) AS is_stale
FROM source_health sh;
