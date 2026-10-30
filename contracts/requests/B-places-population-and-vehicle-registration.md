# Contract change 001: places/population and vehicle registration

**Status: APPROVED by the human on 2026-10-30** (population source: OSM Census tags). Applied on `main`.

- `contracts/db/patches/001_places_population.sql`: B-owned `places` table plus the `v_places` view, for
  A's Needs agent. Population is a lower bound; consumers report coverage.
- `openapi.yaml`: `POST /api/vehicles` (B) lets the Driver PWA register and receive a device
  token. Gate 0 had no way to create the vehicles that telemetry requires.
- `app/core/migrate.py` applies `contracts/db/patches/*.sql` after the baseline, so databases that
  already applied `schema.sql` are not invalidated.
