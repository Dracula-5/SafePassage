-- ReliefRoute contract: cross-owner foreign keys and read views.
-- FROZEN after Gate 0. Applied after all 1xx (A) and 2xx (B) baseline migrations.
-- Cross-owner reads MUST go through these views; nobody writes the other side's tables.

-- Cross-owner FKs (A tables -> B tables). Kept here so 1xx migrations do not depend on 2xx.
ALTER TABLE routes ADD CONSTRAINT routes_vehicle_fk FOREIGN KEY (vehicle_id) REFERENCES vehicles(id);
ALTER TABLE orders ADD CONSTRAINT orders_supplier_fk FOREIGN KEY (supplier_id) REFERENCES suppliers(id);
ALTER TABLE orders ADD CONSTRAINT orders_destination_fk FOREIGN KEY (destination_facility_id) REFERENCES facilities(id);

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
