-- Owner: B. Read-only view of the private admin_areas table, for the Needs population estimate
-- (contract request A-plan-and-admin-views). Lives here, not in contracts/db/patches/, because
-- patches run before owner migrations and admin_areas is created by 202.
CREATE VIEW v_admin_areas AS
SELECT id, source_ref, admin_level, name, geom FROM admin_areas;
