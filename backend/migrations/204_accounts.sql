-- Email accounts for the one web app. Private tables, plus an owner column on B's vehicles and suppliers.
-- Passwords and tokens are stored only as hashes.
CREATE TABLE accounts (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email           text NOT NULL,
    name            text NOT NULL,
    role            text NOT NULL CHECK (role IN ('operator', 'driver', 'supplier')),
    password_hash   text NOT NULL,
    failed_signins  integer NOT NULL DEFAULT 0,
    locked_until    timestamptz,
    disabled_at     timestamptz,
    last_signin_at  timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX accounts_email_idx ON accounts (lower(email));

CREATE TABLE account_sessions (
    token_hash  text PRIMARY KEY,
    account_id  uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    expires_at  timestamptz NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX account_sessions_account_idx ON account_sessions (account_id);

CREATE TABLE password_resets (
    token_hash  text PRIMARY KEY,
    account_id  uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    expires_at  timestamptz NOT NULL
);

ALTER TABLE vehicles ADD COLUMN account_id uuid REFERENCES accounts(id) ON DELETE SET NULL;
ALTER TABLE suppliers ADD COLUMN account_id uuid REFERENCES accounts(id) ON DELETE SET NULL;
CREATE UNIQUE INDEX vehicles_account_idx ON vehicles (account_id) WHERE account_id IS NOT NULL;
CREATE UNIQUE INDEX suppliers_account_idx ON suppliers (account_id) WHERE account_id IS NOT NULL;
