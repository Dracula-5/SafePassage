-- Proof that an address belongs to the person who typed it. Stays empty until a mail provider is set.
ALTER TABLE accounts ADD COLUMN email_verified_at timestamptz;

CREATE TABLE email_checks (
    token_hash  text PRIMARY KEY,
    account_id  uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    expires_at  timestamptz NOT NULL
);
