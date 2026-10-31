# Contract change 003: access control and limits on the REST contract

**Status: PENDING human review.** Requested by the project owner on 2026-10-31 ("complete what's left,
security and other things"). Implemented in owner code. Nothing under `contracts/` changed except this
file, so `openapi.yaml` no longer describes these details until this is approved and applied.

## Why

The contract was written for a local demo and has no security schemes. Reviewing it for a public
deployment found that anyone could register a vehicle, which replans then auto-approve, and could read
driver names, live truck positions and routes.

## Changes to apply to `openapi.yaml`

**New security requirements.** Each is off when its setting is unset in development, and required for
a production start (`core/security.py`).

| Paths | Requirement |
|---|---|
| `POST /api/vehicles` | `X-Enrolment-Code` |
| `GET /api/vehicles`, `/api/plans`, `/api/plans/{id}`, `/api/orders`, `/api/brain/health` | `X-Operator-Token` |
| `GET /api/stream` | `X-Operator-Token`, or `?ticket=` from `POST /api/stream/ticket` |
| `GET /api/vehicles/{id}/assignment` | That vehicle's `X-Device-Token`, or `X-Operator-Token` |

**New endpoints.**

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /api/stream/ticket` | Operator | Five-minute read-only ticket, because EventSource cannot send headers |
| `POST /api/vehicles/{id}/revoke` | Operator | Ends a device token; planning skips the vehicle |
| `POST /api/suppliers/{id}/verify` | Operator | `{"verified": bool}`. `false` also ends the supplier's token |

**New responses, on any path:** `413` body over 1 MB, `429` rate limited with `Retry-After`, and `503`
from `/api/stream` at 50 connections.

**Changed values.**

- `GET /api/sources/health` gains `brain_worker` and `ingestion_worker` rows.
- `GET /api/brain/health` gains an `event_bus` row.
- `GET /api/hazards` is capped at 2,000 rows and `GET /api/orders` at 2,000, newest first.
- Names reject control characters, bidi overrides, `<` and `>` with `422`.

## Who is affected

- **A and B:** both already use the code in place. Clients without a token keep working in development.
- **The driver PWA, dashboard and `sim_driver.py`:** already updated.
- **Database:** no schema change.

## Files outside `contracts/` that this also touches

- `backend/.gitignore` is new, because the frozen root `.gitignore` misses `backend/.env.production`.
- `backend/requirements/constraints.txt` is new and pins exact versions for images and CI.
- `backend/requirements/dev.txt` gains `pypdf`, for reading generated PDFs in tests.
