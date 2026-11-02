-- Town coordinates belong to the simulator (backend/tests/tools/demo_fleet.py): runtime code and its tables hold none.
ALTER TABLE demo_drivers DROP COLUMN lon, DROP COLUMN lat;
