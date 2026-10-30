# Contract request A: views for facilities and suppliers

**From:** Instance A (Brain), during Gate 1. **Status:** awaiting human decision.

## Problem
The master prompt says cross-reads go through contract views. `contracts/db/schema.sql`
has views for hazards, vehicles, stock and source health, but **none for `facilities` or
`suppliers`** (an oversight in Gate 0, which A authored). A needs:
- facilities (shelters, hospitals, depots): Needs agent destinations, route endpoints, alternate hubs;
- suppliers (facility, contact, language): Procurement and Dispatcher (WhatsApp/SMS).

## Proposed addition (additive only; nothing existing changes)
```sql
CREATE VIEW v_facilities AS
SELECT f.id, f.source, f.source_ref, f.kind, f.name, f.geom, f.capacity_people, f.attributes,
       f.fetched_at, EXISTS (SELECT 1 FROM suppliers s WHERE s.facility_id = f.id) AS is_supplier
FROM facilities f;

CREATE VIEW v_suppliers AS
SELECT s.id, s.facility_id, s.name, s.contact_phone, s.contact_whatsapp, s.preferred_language,
       s.verified, f.geom, f.name AS facility_name
FROM suppliers s JOIN facilities f ON f.id = s.facility_id;
```
Note: `portal_token_hash` is deliberately not exposed.

## How to apply
Append to `contracts/db/schema.sql` only if no database has applied it yet. Otherwise ship it as a
contract migration (e.g. `contracts/db/patches/001_views.sql`); the migrate runner would need
to pick that up. That is A-owned code, and A will do it once you approve.

## Impact
- B: none. B's tables are only read.
- A: until approved, A code does not read `facilities` or `suppliers`. Gate 1 does not need them.
