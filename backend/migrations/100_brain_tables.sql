-- Owner: Instance A (Brain). Gate 0 baseline; columns here are part of the contract
-- (see contracts/db/SCHEMA.md). Later 1xx migrations may ADD columns/indexes only.
-- Foreign keys to B-owned tables are added in contracts/db/900_contract_views.sql.
-- LangGraph checkpoint tables are created by langgraph-checkpoint-postgres setup(), not here.

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
    vehicle_id          uuid NOT NULL,                     -- FK -> vehicles (contract)
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
    supplier_id             uuid NOT NULL,                 -- FK -> suppliers (contract)
    destination_facility_id uuid NOT NULL,                 -- FK -> facilities (contract)
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
