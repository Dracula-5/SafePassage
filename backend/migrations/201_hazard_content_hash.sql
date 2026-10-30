-- Owner: B. Lets the ingestion upsert detect "same alert re-polled, nothing changed" cheaply,
-- so version (and hazard.updated) only moves on real changes.
ALTER TABLE hazards ADD COLUMN content_hash text;
