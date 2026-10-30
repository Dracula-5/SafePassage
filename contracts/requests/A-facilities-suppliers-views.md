# Contract request A: views for facilities and suppliers

**Status: APPROVED by the human on 2026-10-30 and applied to `contracts/db/schema.sql` on `main`.**

Adds `v_facilities` (with `is_supplier`) and `v_suppliers` (without `portal_token_hash`) so A can
read facilities and suppliers without touching B's tables. Additive only; no B table changed.
