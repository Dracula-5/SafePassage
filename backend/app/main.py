"""FastAPI entry point.

Routers are discovered, not hand-wired: each instance exposes `router` from its own package
(app.api_brain.router for A, app.api_senses.router for B), so neither side edits this file
to add endpoints. A package that does not exist yet is skipped with a warning; a package that
exists but fails to import is a real bug and crashes startup loudly.
"""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .core.bus import close_redis, get_redis
from .core.config import get_common_settings
from .core.db import close_pool, get_pool
from .core.logging import configure_logging, get_logger
from .core.security import BodyLimit, RateLimit, SecurityHeaders, problem, production_problems

log = get_logger(__name__)

ROUTER_MODULES = ("app.api_brain.router", "app.api_senses.router")
CORS_METHODS = ["GET", "POST", "DELETE"]
CORS_HEADERS = ["Content-Type", "X-Operator-Token", "Idempotency-Key", "Last-Event-ID"]


def load_router(module_name: str) -> APIRouter | None:
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        # Only swallow "this package/module itself is absent", never a missing dependency inside it.
        parts = module_name.split(".")
        own_names = {".".join(parts[: i + 1]) for i in range(1, len(parts))}
        if exc.name in own_names:
            log.warning("router_module_absent", module=module_name)
            return None
        raise
    router = getattr(module, "router", None)
    if not isinstance(router, APIRouter):
        raise TypeError(f"{module_name}.router must be a fastapi.APIRouter")
    return router


def install_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return problem(exc.status_code, exc.__class__.__name__ if exc.status_code >= 500 else "Request error", exc.detail)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        return problem(422, "Validation error", errors)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        # The details belong in the log, never in a response: they can name tables, hosts and paths.
        log.error("unhandled_error", path=request.url.path, error=type(exc).__name__, exc_info=exc)
        return problem(500, "Internal error", "something went wrong on our side")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Connections are lazy; the API starts even when Postgres/Redis are down and /health says so.
    yield
    await close_pool()
    await close_redis()


def create_app(router_modules: tuple[str, ...] = ROUTER_MODULES) -> FastAPI:
    settings = get_common_settings()
    configure_logging(settings.log_level)
    if problems := production_problems(settings):
        raise RuntimeError("Refusing to start in production:\n  - " + "\n  - ".join(problems))
    production = settings.app_env == "production"
    app = FastAPI(title="ReliefRoute API", version="0.1.0", lifespan=lifespan, docs_url=None if production else "/docs",
                  redoc_url=None if production else "/redoc", openapi_url=None if production else "/openapi.json")  # fmt: skip
    install_problem_handlers(app)
    # Last added runs first. The rate limit stays inside CORS so a browser can read its 429.
    app.add_middleware(RateLimit, enabled=settings.rate_limit_enabled)
    app.add_middleware(BodyLimit, limit=settings.max_body_bytes)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=CORS_METHODS, allow_headers=CORS_HEADERS
        )
    app.add_middleware(SecurityHeaders, production=production)

    @app.get("/health", tags=["core"])
    async def health() -> JSONResponse:
        checks: dict[str, str] = {}
        try:
            pool = await get_pool()
            await pool.fetchval("SELECT 1")
            checks["postgres"] = "ok"
        except Exception as exc:  # report, never crash the health endpoint
            log.warning("health_postgres_failed", error=str(exc))
            checks["postgres"] = "error"
        try:
            await get_redis().ping()
            checks["redis"] = "ok"
        except Exception as exc:
            log.warning("health_redis_failed", error=str(exc))
            checks["redis"] = "error"
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)

    for module_name in router_modules:
        router = load_router(module_name)
        if router is not None:
            app.include_router(router)
            log.info("router_registered", module=module_name, routes=len(router.routes))
    return app


app = create_app()
