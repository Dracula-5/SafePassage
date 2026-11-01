"""Who may do what.

  signed-in account  rr_session cookie (core/sessions.py): what people use, in the web app
  operator token     X-Operator-Token: the same access for scripts and the runbook in docs/security.md
  enrolment code     X-Enrolment-Code on the legacy POST /api/vehicles
  device token       X-Device-Token (telemetry/): one vehicle's own GPS and assignment

Anyone may sign up as a supplier and use the portal; supplier verification is separate. Duty officer and
driver accounts stay pending until an approved duty officer or the operator token approves them. Pending
accounts cannot sign in or pass role checks: a replan auto-approves vehicles, so a stranger's phone must
never get onto the fleet.

The token and code are off when unset, for local development. A production start refuses to run without
them (security.production_problems).
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Header, HTTPException, Request

from .config import get_common_settings
from .logging import get_logger
from .sessions import account_for

log = get_logger(__name__)


def same_secret(expected: str, given: str | None) -> bool:
    # As bytes: compare_digest raises TypeError on non-ASCII str, and a header can hold anything.
    return hmac.compare_digest(expected.encode(), (given or "").encode())


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def refuse(request: Request, kind: str, detail: str) -> HTTPException:
    log.warning("auth_failed", kind=kind, path=request.url.path, client=client_ip(request))
    return HTTPException(401, detail)


async def is_operator(request: Request) -> bool:
    account = await account_for(request)
    return account is not None and account["role"] == "operator"


async def require_operator(request: Request, x_operator_token: Annotated[str | None, Header()] = None) -> None:
    expected = get_common_settings().operator_token
    if expected and not same_secret(expected, x_operator_token) and not await is_operator(request):
        raise refuse(request, "operator", "sign in as an operator")
    if request.method != "GET":
        log.info("operator_action", method=request.method, path=request.url.path, client=client_ip(request))


async def require_authorized_operator(
    request: Request, x_operator_token: Annotated[str | None, Header()] = None
) -> None:
    if not await is_operator(request):
        expected = get_common_settings().operator_token
        if not expected or not same_secret(expected, x_operator_token):
            raise refuse(request, "operator", "sign in as an approved duty officer")
    if request.method != "GET":
        log.info("operator_action", method=request.method, path=request.url.path, client=client_ip(request))


async def require_enrolment(request: Request, x_enrolment_code: Annotated[str | None, Header()] = None) -> None:
    expected = get_common_settings().enrolment_code
    if expected and not same_secret(expected, x_enrolment_code):
        raise refuse(request, "enrolment", "enrolment code required: ask your dispatcher for it")


def require_role(role: str) -> Callable[[Request], Awaitable[dict]]:
    async def signed_in(request: Request) -> dict:
        account = await account_for(request)
        if account is None or account["role"] != role:
            raise refuse(request, role, f"sign in with a {role} account")
        return account

    return signed_in
