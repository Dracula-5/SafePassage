# Contract change 005: account approval instead of sign-up codes

**Status: PENDING human review.** Builds on request 004 (`B-accounts-and-one-web-app.md`), which is also pending. Implemented in owner code (`backend/app/accounts/router.py`, `backend/app/core/auth.py`, `backend/app/core/sessions.py`) and migration `209_account_approval.sql`. Nothing under `contracts/` is changed except this file, so `openapi.yaml` does not describe any of this until it is approved and applied.

## Why

Duty officer and driver sign-up used a shared code typed at sign-up (`ENROLMENT_CODE` for drivers, `OPERATOR_TOKEN` for duty officers). Anyone who had the code, or a leaked copy of it, got an account straight away, and the code had to be given to the person in advance. The owner asked for sign-up with no codes. The replacement is a server-side approval: anyone can ask for a duty officer or driver account, but it cannot sign in, hold a session or pass any role check until an approved duty officer (or the operator token, for scripts) approves it. Supplier accounts stay as they are: they are usable at once and verified separately.

## Changes to apply to `openapi.yaml`

**`POST /api/auth/signup`**
- Request body: remove `code`. The body is now `email`, `password`, `name`, `role`.
- Response (201): add `approved: boolean`. Supplier is `true`. Duty officer and driver are `false` until approved. The session cookie is set only when `approved` is `true`.
- Errors: the 401 "that is not the … code" response no longer exists.

**`POST /api/auth/signin`**
- New response `403` with detail "your account is pending approval by a duty officer". A wrong password still gets 401, and the password is checked before approval, so nobody learns whether a pending account exists without its password.

**`GET /api/auth/me`, and every `Account` object returned** (`signin`, `signup`, `me`)
- Add `approved: boolean`.

**`POST /api/accounts/{id}/approve`** (new)
- Auth: a signed-in approved operator, or `X-Operator-Token`. Anything else is 401.
- Response 200: `{ "id": "...", "approved": true }`. Approving an account that is already approved is a no-op.
- 404 if the account does not exist. 409 for a supplier account, which never needs approval.

**`GET /api/accounts`**
- Each item gains `approved_at` (timestamp or null) and the UI reads it as "Pending approval".

## Who is affected

- **Database, migration 209:** `ALTER TABLE accounts ADD COLUMN approved_at timestamptz` and `UPDATE accounts SET approved_at = created_at`. Every account that exists today is approved, so nobody is locked out by this migration. Owner's own migration under `backend/migrations/2xx`, so no `db/SCHEMA.md` column is dropped or retyped.
- **Sessions:** a session row no longer authenticates an account whose `approved_at` is null (`core/sessions.py`). This also covers an account approved later from an old session.
- **Operator and driver web screens:** a new sign-up shows "pending approval" and no session is created. The Team tab gets an "Approve account" action for pending duty officers and drivers.
- **`X-Enrolment-Code` on the legacy `POST /api/vehicles`:** left as it is. The enrolment code still exists for that endpoint only, and its removal is a separate decision.
- **Deployment:** the production start-up check (`security.production_problems`) still requires `OPERATOR_TOKEN` and `ENROLMENT_CODE`. Those are still used for the operator-token approval path and the legacy endpoint, so they stay required. This request does not change that check.

## Migration plan

1. Owner reviews this and request 004 together, since they describe the same accounts API.
2. Apply `209_account_approval.sql` (already applied to the local database; it is idempotent in effect because it only backfills approved rows).
3. Update `openapi.yaml` as listed above, and regenerate nothing: no enum or event schema changes.
4. Frontend and backend tests: sign-up tests that used a code are replaced with "pending, then approved by the operator token, then signed in".

## Known limits, for the reviewer

- A stranger can create a pending duty officer account and fill the approval queue. That is the trade-off of having no code. Pending accounts have no access, and they are visible only to approved duty officers.
- The 403 on a correct-password sign-in tells the person that the account exists and is waiting. That is the same information the approval screen already gives, so it is accepted.
