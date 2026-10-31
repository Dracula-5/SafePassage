"""Who may read one vehicle's assignment: that vehicle's own phone, or an operator.

A route says where a truck of relief goods will be and when, so a guessed vehicle id must not
reveal it. B owns vehicles, so A's router uses this instead of reading the token hash itself.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Header, Request

from app.core.auth import refuse, same_secret
from app.core.config import get_common_settings
from app.core.db import get_pool

from .tokens import hash_token


async def require_vehicle_access(
    vehicle_id: UUID,
    request: Request,
    x_device_token: Annotated[str | None, Header()] = None,
    x_operator_token: Annotated[str | None, Header()] = None,
) -> None:
    expected = get_common_settings().operator_token
    if not expected or same_secret(expected, x_operator_token):
        return
    if x_device_token:
        owner = await (await get_pool()).fetchval(
            "SELECT id FROM vehicles WHERE device_token_hash = $1", hash_token(x_device_token))
        if owner == vehicle_id:
            return
    raise refuse(request, "vehicle", "this vehicle's device token or an operator token is required")
