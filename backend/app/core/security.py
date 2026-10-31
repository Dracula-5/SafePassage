"""Protections every route gets, and the refusal to start with unsafe production settings.

Plain ASGI middleware: Starlette's BaseHTTPMiddleware buffers streaming responses, which would hold
back /api/stream and hide client disconnects.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .bus import get_redis
from .config import CommonSettings
from .logging import get_logger

log = get_logger(__name__)
MIN_OPERATOR_TOKEN_CHARS = 32
MIN_ENROLMENT_CODE_CHARS = 12
DEFAULT_DB_PASSWORD = ":reliefroute@"
UNPROTECTED_DOC_PATHS = ("/docs", "/redoc")  # development only: Swagger's page loads from a CDN
HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "permissions-policy": "geolocation=(self), camera=(), microphone=()",
    "cross-origin-opener-policy": "same-origin",
}
CSP = "; ".join((
    "default-src 'self'", "script-src 'self'", "style-src 'self' 'unsafe-inline'", "img-src 'self' data:",
    "connect-src 'self'", "object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'", "form-action 'self'",
))  # fmt: skip
HSTS = "max-age=31536000; includeSubDomains"


def problem(status: int, title: str, detail: object = None, headers: dict[str, str] | None = None) -> JSONResponse:
    """RFC 9457-style error body, the contract's Problem schema."""
    body: dict = {"type": "about:blank", "title": title, "status": status}
    if detail is not None:
        body["detail"] = detail if isinstance(detail, str) else str(detail)
    return JSONResponse(body, status_code=status, media_type="application/problem+json", headers=headers)


def production_problems(s: CommonSettings) -> list[str]:
    """Everything that would leave a public deployment open. Reported together, so one restart fixes all."""
    if s.app_env != "production":
        return []
    found = []
    if len(s.operator_token or "") < MIN_OPERATOR_TOKEN_CHARS:
        found.append(f"OPERATOR_TOKEN must be at least {MIN_OPERATOR_TOKEN_CHARS} characters")
    if len(s.enrolment_code or "") < MIN_ENROLMENT_CODE_CHARS:
        found.append(f"ENROLMENT_CODE must be at least {MIN_ENROLMENT_CODE_CHARS} characters")
    if any(not origin.startswith("https://") for origin in s.cors_origins):
        found.append("CORS_ORIGINS may only list https:// origins, and never *")
    if DEFAULT_DB_PASSWORD in s.database_url:
        found.append("DATABASE_URL still uses the development password")
    if "@" not in s.redis_url:
        found.append("REDIS_URL has no password")
    if not s.rate_limit_enabled:
        found.append("RATE_LIMIT_ENABLED must stay on")
    return found


class SecurityHeaders:
    def __init__(self, app: ASGIApp, *, production: bool) -> None:
        self.app = app
        self.production = production

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in HEADERS.items():
                    headers.setdefault(name, value)
                if scope["path"] not in UNPROTECTED_DOC_PATHS:
                    headers.setdefault("content-security-policy", CSP)
                if scope["path"].startswith("/api/"):  # drivers' positions must not sit in a shared cache
                    headers.setdefault("cache-control", "no-store")
                if self.production:
                    headers.setdefault("strict-transport-security", HSTS)
            await send(message)

        await self.app(scope, receive, with_headers)


class BodyLimit:
    """Nothing legitimate is near this: 500 GPS fixes are about 100 kB. Counts chunks as well as
    trusting Content-Length, because a chunked upload does not send one."""

    def __init__(self, app: ASGIApp, *, limit: int) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = Headers(scope=scope).get("content-length", "")
        if declared.isdigit() and int(declared) > self.limit:
            await problem(413, "Request error", "request body too large")(scope, receive, send)
            return
        seen = 0

        async def counted() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.limit:
                    raise HTTPException(413, "request body too large")
            return message

        await self.app(scope, counted, send)


@dataclass(frozen=True)
class Limit:
    count: int
    window_s: int


DEFAULT_LIMIT = Limit(1200, 60)  # a busy dashboard stays far below this
# Generous for sign-ups: a depot's drivers can share one address, and a limit must not lock them out in
# a disaster. The enrolment code is what stops guessing; this only slows a flood.
LIMITS = {
    ("POST", "/api/vehicles"): Limit(60, 3600),
    ("POST", "/portal/api/register"): Limit(60, 3600),
    ("POST", "/portal/api/stock"): Limit(60, 60),
    ("POST", "/portal/whatsapp"): Limit(120, 60),
    ("GET", "/portal/api/facilities"): Limit(60, 60),
    ("GET", "/api/stream"): Limit(30, 60),
    ("POST", "/api/stream/ticket"): Limit(30, 60),
}
NOT_LIMITED = ("/health",)
REDIS_WAIT_S = 0.25
REDIS_RETRY_AFTER_S = 5.0
MAX_LOCAL_KEYS = 10_000


class RateLimit:
    """Fixed window per client address, shared through Redis. If Redis is down, each process counts
    for itself: limits must survive an outage, and a slow limiter must not become one."""

    def __init__(self, app: ASGIApp, *, enabled: bool = True) -> None:
        self.app = app
        self.enabled = enabled
        self.local: dict[str, int] = {}
        self.skip_redis_until = 0.0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.enabled or scope["path"] in NOT_LIMITED:
            await self.app(scope, receive, send)
            return
        limit = LIMITS.get((scope["method"], scope["path"]))
        bucket = f"{scope['method']}:{scope['path']}" if limit else "*"  # one bucket, so paths can't mint keys
        limit = limit or DEFAULT_LIMIT
        now = time.time()
        client = (scope.get("client") or ("unknown",))[0]
        key = f"ratelimit:{bucket}:{client}:{int(now // limit.window_s)}"
        if await self._hit(key, limit.window_s) > limit.count:
            retry_after = limit.window_s - int(now % limit.window_s)
            log.warning("rate_limited", path=scope["path"], client=client)
            response = problem(429, "Too many requests", "slow down and try again shortly", {"Retry-After": str(retry_after)})
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def _hit(self, key: str, window_s: int) -> int:
        if time.monotonic() >= self.skip_redis_until:
            try:
                return await asyncio.wait_for(self._hit_redis(key, window_s), REDIS_WAIT_S)
            except (RedisError, OSError, TimeoutError) as exc:
                self.skip_redis_until = time.monotonic() + REDIS_RETRY_AFTER_S
                log.warning("rate_limit_counting_locally", error=type(exc).__name__)
        if len(self.local) >= MAX_LOCAL_KEYS:
            self.local.clear()
        self.local[key] = self.local.get(key, 0) + 1
        return self.local[key]

    @staticmethod
    async def _hit_redis(key: str, window_s: int) -> int:
        async with get_redis().pipeline(transaction=False) as pipe:
            pipe.incr(key)
            pipe.expire(key, window_s)
            count, _ = await pipe.execute()
        return count
