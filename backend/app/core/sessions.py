"""Email accounts: password hashing and the session behind the rr_session cookie.

A session is a random token stored only as a hash, so a leaked table cannot be replayed, and it can be
ended from the server, which a signed token could not. The cookie is HttpOnly, so a script on the page
cannot read it, and SameSite=Lax, so another site cannot send it along with a form.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
from uuid import UUID

from fastapi import Request, Response

from .config import get_common_settings
from .db import get_pool

COOKIE = "rr_session"
SESSION_DAYS = 7
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
# About 32 MB and 100 ms a guess: costly to attack, and fine for a sign-in.
SCRYPT = {"n": 2**15, "r": 8, "p": 1, "maxmem": 2**26}


def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)


def _hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    return f"{base64.b64encode(salt).decode()}${base64.b64encode(_scrypt(password, salt)).decode()}"


def _matches(password: str, stored: str) -> bool:
    salt, _, digest = stored.partition("$")
    return hmac.compare_digest(_scrypt(password, base64.b64decode(salt)), base64.b64decode(digest))


# Hashing takes real time, so it runs in a thread to leave other requests alone.
async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hash, password)


async def password_matches(password: str, stored: str) -> bool:
    return await asyncio.to_thread(_matches, password, stored)


NO_SUCH_ACCOUNT = _hash(secrets.token_urlsafe(16))  # checked anyway, so a wrong email takes as long as a wrong password


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def open_session(response: Response, request: Request, account_id: UUID) -> None:
    token = secrets.token_urlsafe(32)
    await (await get_pool()).execute(
        """INSERT INTO account_sessions (token_hash, account_id, expires_at)
           VALUES ($1, $2, now() + make_interval(days => $3))""", digest(token), account_id, SESSION_DAYS)  # fmt: skip
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86_400, httponly=True, samesite="lax", path="/",
                        secure=request.url.scheme == "https")  # fmt: skip


async def close_session(response: Response, request: Request) -> None:
    if token := request.cookies.get(COOKIE):
        await (await get_pool()).execute("DELETE FROM account_sessions WHERE token_hash = $1", digest(token))
    response.delete_cookie(COOKIE, path="/")


def approval_required() -> bool:
    """Duty officer and driver accounts wait for a duty officer's approval everywhere except the local
    development setup (APP_ENV=development). Anything else, including a typo, keeps the approval gate."""
    return get_common_settings().app_env != "development"


async def account_for(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    # A second line against forged requests: browsers say when another site started this one.
    if request.method not in SAFE_METHODS and request.headers.get("sec-fetch-site") == "cross-site":
        return None
    row = await (await get_pool()).fetchrow(
        """SELECT a.id, a.email, a.name, a.role, a.approved_at FROM account_sessions s JOIN accounts a ON a.id = s.account_id
           WHERE s.token_hash = $1 AND s.expires_at > now() AND a.disabled_at IS NULL AND (NOT $2 OR a.approved_at IS NOT NULL)""",
        digest(token), approval_required(),
    )  # fmt: skip
    return dict(row) if row else None
