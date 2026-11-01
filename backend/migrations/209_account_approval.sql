ALTER TABLE accounts ADD COLUMN approved_at timestamptz;

UPDATE accounts SET approved_at = created_at;
