# Contract change 005: email confirmation and forgot-password

**Status: PENDING human review.** Implemented in owner code and migration 205, building on request 004. Nothing under `contracts/` changed except this file. All of it stays off until `SMTP_HOST` and `MAIL_FROM` are set.

## Changes to apply to `openapi.yaml`

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /api/auth/options` | Public | Says whether email is on, so the screens can offer the right help |
| `POST /api/auth/forgot` | Public | `202` whether or not the account exists. Emails a one-time reset link |
| `POST /api/auth/verify` | One-time link | Confirms an address |

`GET /api/accounts` gains `email_verified`.

## Who is affected

- **Database:** migration 205 adds `accounts.email_verified_at` and a private `email_checks` table.
- **Settings:** optional `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM` and `PUBLIC_URL`.
