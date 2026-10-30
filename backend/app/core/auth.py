"""Shared operator token for endpoints that change the world (drills, plans, approvals).

Off when OPERATOR_TOKEN is unset, for local development. Any public deployment must set it: the
approval gate means nothing if anyone on the internet can press Approve.
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Header, HTTPException

from .config import get_common_settings


async def require_operator(x_operator_token: Annotated[str | None, Header()] = None) -> None:
    expected = get_common_settings().operator_token
    if expected and not hmac.compare_digest(expected, x_operator_token or ""):
        raise HTTPException(401, "operator token required")
