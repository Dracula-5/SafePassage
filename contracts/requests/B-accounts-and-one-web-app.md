# Contract change 004: email accounts and one web app

**Status: PENDING human review.** Requested by the project owner on 2026-10-31 ("all these apps should be in one web app... authentication sign up sign in with emails"). Implemented in owner code and migration 204. Nothing under `contracts/` changed except this file, so `openapi.yaml` does not describe these until this is approved and applied. This builds on request 003.

## Why

The dashboard, driver app and supplier portal were three separate pages behind shared secrets. They are now one Next.js app where each person signs in with an email address.

## Changes to apply to `openapi.yaml`

**New endpoints.**

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /api/auth/signup` | Public; supplier signs in, operator and driver await approval | Creates an account |
| `POST /api/auth/signin`, `/signout`, `GET /api/auth/me` | Session | Signing in and out |
| `POST /api/auth/reset` | One-time link | Sets a new password |
| `GET /api/accounts` | Operator | Everyone with an account |
| `POST /api/accounts/{id}/approve`, `/access`, `/reset-link` | Approved operator or operator token | Approve an account, remove or restore access, or make a reset link |
| `GET`, `POST /api/driver/vehicle`, `POST /api/driver/vehicle/device` | Driver | The signed-in driver's own vehicle and this phone's token |
| `GET`, `POST /api/supplier/me`, `POST /api/supplier/stock` | Supplier | The signed-in supplier's own organisation and stock |
| `GET /api/status` | Public | Small live counts, for refreshing every 5 seconds |
| `GET /api/overview` | Operator | Live operations counts |

**Changed.**

- Everything that needs `X-Operator-Token` also accepts a signed-in operator.
- `POST /api/plans/{id}/approve` records the signed-in account, and ignores `decided_by` for a session.

**Removed.**

- `GET /portal`, `GET /driver` and `GET /driver/{path}`. These pages are now `/supplier` and `/driver` in the web app. `/portal/api/*` and `/portal/whatsapp` stay.
- `POST /api/stream/ticket` from request 003: same-origin cookies reach EventSource.

## Who is affected

- **Database:** new private tables `accounts`, `account_sessions` and `password_resets`, and an `account_id` owner column on `vehicles` and `suppliers`, in migration 204.
- **`contracts/ownership.txt`:** still lists `driver-pwa/*`, which no longer exists. The driver screen is under `frontend/`.
