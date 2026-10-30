-- Owner: A. Additive columns for planning and dispatch.
ALTER TABLE routes ADD COLUMN instructions jsonb;          -- {language, text, source_text} for the driver
ALTER TABLE notifications ADD COLUMN route_id uuid REFERENCES routes(id) ON DELETE SET NULL;
ALTER TABLE plans ADD COLUMN escalations jsonb NOT NULL DEFAULT '[]';   -- shortfalls / unreachable items for humans
ALTER TABLE plans ADD COLUMN triage jsonb;                 -- validated Sentinel output
