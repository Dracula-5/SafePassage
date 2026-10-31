"""Sign-up, sign-in and account management for the web app.

No email is sent: that needs a mail provider, so addresses are not proven and a forgotten password is
reset by a one-time link that an operator creates and passes on. See docs/security.md.
"""

from __future__ import annotations

import secrets
from typing import Annotated, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.core.auth import refuse, require_operator, same_secret
from app.core.config import get_common_settings
from app.core.db import get_pool
from app.core.sessions import (
    NO_SUCH_ACCOUNT, account_for, close_session, digest, hash_password, open_session, password_matches,
)  # fmt: skip
from app.core.validation import Email, Name

router = APIRouter(tags=["senses"])
MAX_FAILED_SIGNINS = 10
LOCKED_MINUTES = 15
RESET_LINK_HOURS = 24
Password = Annotated[str, Field(min_length=10, max_length=128)]


def account_out(row) -> dict:
    return {"id": str(row["id"]), "email": row["email"], "name": row["name"], "role": row["role"]}


class SignUp(BaseModel):
    email: Email
    password: Password
    name: Name = Field(min_length=2, max_length=100)
    role: Literal["operator", "driver", "supplier"]
    code: str | None = Field(default=None, max_length=200)


class SignIn(BaseModel):
    email: Email
    password: str = Field(min_length=1, max_length=128)


@router.post("/api/auth/signup", status_code=201)
async def sign_up(body: SignUp, request: Request, response: Response) -> dict:
    settings = get_common_settings()
    required = {"operator": settings.operator_token, "driver": settings.enrolment_code}.get(body.role)
    if required and not same_secret(required, body.code):
        raise refuse(request, "signup", f"that is not the {body.role} code: ask your dispatcher for it")
    password_hash = await hash_password(body.password)
    try:
        row = await (await get_pool()).fetchrow(
            """INSERT INTO accounts (email, name, role, password_hash, last_signin_at)
               VALUES ($1, $2, $3, $4, now()) RETURNING id, email, name, role""",
            body.email, body.name, body.role, password_hash,
        )  # fmt: skip
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(409, "an account with this email already exists: sign in instead") from exc
    await open_session(response, request, row["id"])
    return account_out(row)


@router.post("/api/auth/signin")
async def sign_in(body: SignIn, request: Request, response: Response) -> dict:
    pool = await get_pool()
    row = await pool.fetchrow(
        """SELECT id, email, name, role, password_hash, disabled_at, locked_until > now() AS locked
           FROM accounts WHERE lower(email) = $1""", body.email)  # fmt: skip
    if row and row["locked"]:
        raise HTTPException(429, f"too many wrong passwords: try again in {LOCKED_MINUTES} minutes")
    correct = await password_matches(body.password, row["password_hash"] if row else NO_SUCH_ACCOUNT)
    if not (row and correct) or row["disabled_at"]:
        if row:
            await pool.execute(
                """UPDATE accounts SET failed_signins = failed_signins + 1,
                       locked_until = CASE WHEN failed_signins + 1 >= $2 THEN now() + make_interval(mins => $3) END
                   WHERE id = $1""", row["id"], MAX_FAILED_SIGNINS, LOCKED_MINUTES)  # fmt: skip
        raise refuse(request, "signin", "wrong email or password")
    await pool.execute("""UPDATE accounts SET failed_signins = 0, locked_until = NULL, last_signin_at = now()
                          WHERE id = $1""", row["id"])  # fmt: skip
    await open_session(response, request, row["id"])
    return account_out(row)


@router.post("/api/auth/signout", status_code=204)
async def sign_out(request: Request, response: Response) -> None:
    await close_session(response, request)


@router.get("/api/auth/me")
async def who_am_i(request: Request) -> dict:
    account = await account_for(request)
    if account is None:
        raise HTTPException(401, "not signed in")
    return account_out(account)


class PasswordReset(BaseModel):
    token: str = Field(min_length=20, max_length=100)
    password: Password


@router.post("/api/auth/reset", status_code=204)
async def reset_password(body: PasswordReset) -> None:
    password_hash = await hash_password(body.password)
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        account_id = await conn.fetchval(
            "DELETE FROM password_resets WHERE token_hash = $1 AND expires_at > now() RETURNING account_id", digest(body.token))
        if account_id is None:
            raise HTTPException(422, "this link has expired or was already used: ask an operator for a new one")
        await conn.execute("UPDATE accounts SET password_hash = $2, failed_signins = 0, locked_until = NULL WHERE id = $1",
                           account_id, password_hash)  # fmt: skip
        await conn.execute("DELETE FROM account_sessions WHERE account_id = $1", account_id)  # whoever had it is signed out


@router.get("/api/accounts", dependencies=[Depends(require_operator)])
async def list_accounts() -> list[dict]:
    rows = await (await get_pool()).fetch(
        """SELECT a.id, a.email, a.name, a.role, a.last_signin_at, a.disabled_at, s.id AS supplier_id,
                  s.name AS supplier_name, s.verified AS supplier_verified, v.label AS vehicle_label
           FROM accounts a LEFT JOIN suppliers s ON s.account_id = a.id LEFT JOIN vehicles v ON v.account_id = a.id
           ORDER BY a.role, lower(a.email)""")
    return [account_out(r) | {
        "last_signin_at": r["last_signin_at"], "disabled": r["disabled_at"] is not None, "supplier_id": r["supplier_id"],
        "supplier_name": r["supplier_name"], "supplier_verified": r["supplier_verified"], "vehicle_label": r["vehicle_label"],
    } for r in rows]  # fmt: skip


class AccountAccess(BaseModel):
    disabled: bool


@router.post("/api/accounts/{account_id}/access", dependencies=[Depends(require_operator)])
async def set_account_access(account_id: UUID, body: AccountAccess, request: Request) -> dict:
    me = await account_for(request)
    if me and me["id"] == account_id:
        raise HTTPException(409, "you cannot disable your own account")
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        found = await conn.fetchval(
            "UPDATE accounts SET disabled_at = CASE WHEN $2 THEN now() END WHERE id = $1 RETURNING id", account_id, body.disabled)
        if found is None:
            raise HTTPException(404, "account not found")
        if body.disabled:
            await conn.execute("DELETE FROM account_sessions WHERE account_id = $1", account_id)
    return {"id": str(account_id), "disabled": body.disabled}


@router.post("/api/accounts/{account_id}/reset-link", dependencies=[Depends(require_operator)])
async def create_reset_link(account_id: UUID) -> dict:
    token = secrets.token_urlsafe(32)
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        if not await conn.fetchval("SELECT 1 FROM accounts WHERE id = $1", account_id):
            raise HTTPException(404, "account not found")
        await conn.execute("DELETE FROM password_resets WHERE account_id = $1", account_id)
        await conn.execute("""INSERT INTO password_resets (token_hash, account_id, expires_at)
                              VALUES ($1, $2, now() + make_interval(hours => $3))""",
                           digest(token), account_id, RESET_LINK_HOURS)  # fmt: skip
    return {"path": f"/reset?token={token}", "expires_in_hours": RESET_LINK_HOURS}
