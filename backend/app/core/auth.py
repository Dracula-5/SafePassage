"""Who may do what.

  operator token   X-Operator-Token: drills, plans, approvals, and reads that expose drivers or goods
  stream ticket    ?ticket=: those same reads for EventSource, which cannot send headers
  enrolment code   X-Enrolment-Code: registering a vehicle; handed to drivers out of band
  device token     X-Device-Token (telemetry/): one vehicle's own GPS and assignment

Each is off when its setting is unset, for local development. A production start refuses to run
without them (security.production_problems): the approval gate means nothing if anyone on the
internet can press Approve, and a replan auto-approves vehicles, so a stranger's phone must never
get onto the fleet.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Annotated

from fastapi import Header, HTTPException, Query, Request

from .config import get_common_settings
from .logging import get_logger

log = get_logger(__name__)
STREAM_TICKET_TTL_S = 300
MAX_TICKET_EXPIRY_DIGITS = 12


def same_secret(expected: str, given: str | None) -> bool:
    # As bytes: compare_digest raises TypeError on non-ASCII str, and a header can hold anything.
    return hmac.compare_digest(expected.encode(), (given or "").encode())


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def refuse(request: Request, kind: str, detail: str) -> HTTPException:
    log.warning("auth_failed", kind=kind, path=request.url.path, client=client_ip(request))
    return HTTPException(401, detail)


async def require_operator(request: Request, x_operator_token: Annotated[str | None, Header()] = None) -> None:
    expected = get_common_settings().operator_token
    if expected and not same_secret(expected, x_operator_token):
        raise refuse(request, "operator", "operator token required")
    if request.method != "GET":
        log.info("operator_action", method=request.method, path=request.url.path, client=client_ip(request))


async def require_enrolment(request: Request, x_enrolment_code: Annotated[str | None, Header()] = None) -> None:
    expected = get_common_settings().enrolment_code
    if expected and not same_secret(expected, x_enrolment_code):
        raise refuse(request, "enrolment", "enrolment code required: ask your dispatcher for it")


def _sign_ticket(key: str, expires: int) -> str:
    return hmac.new(key.encode(), f"stream:{expires}".encode(), hashlib.sha256).hexdigest()


def stream_ticket() -> dict:
    """Short-lived and read-only, so it is safe in a URL where the operator token would not be."""
    key = get_common_settings().operator_token
    if not key:
        return {"ticket": None, "expires_in": None}
    expires = int(time.time()) + STREAM_TICKET_TTL_S
    return {"ticket": f"{expires}.{_sign_ticket(key, expires)}", "expires_in": STREAM_TICKET_TTL_S}


def valid_stream_ticket(key: str, ticket: str | None) -> bool:
    expires, _, signature = (ticket or "").partition(".")
    if not (expires.isascii() and expires.isdigit() and len(expires) <= MAX_TICKET_EXPIRY_DIGITS):
        return False
    return int(expires) > time.time() and same_secret(_sign_ticket(key, int(expires)), signature)


async def require_stream_access(
    request: Request,
    ticket: Annotated[str | None, Query(max_length=128)] = None,
    x_operator_token: Annotated[str | None, Header()] = None,
) -> None:
    key = get_common_settings().operator_token
    if key and not (same_secret(key, x_operator_token) or valid_stream_ticket(key, ticket)):
        raise refuse(request, "stream", "operator token or a fresh stream ticket required")
