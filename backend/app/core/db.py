"""asyncpg connection pool, created lazily so the API can start while Postgres is down."""

from __future__ import annotations

import asyncio
import json

import asyncpg

from .config import get_common_settings
from .logging import get_logger

log = get_logger(__name__)
_pool: asyncpg.Pool | None = None
_lock = asyncio.Lock()


async def _init_connection(conn: asyncpg.Connection) -> None:
    # jsonb <-> Python objects; geometry is always selected via ST_AsGeoJSON / ST_X / ST_Y.
    for typ in ("json", "jsonb"):
        await conn.set_type_codec(typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    async with _lock:
        if _pool is None:
            settings = get_common_settings()
            _pool = await asyncpg.create_pool(
                settings.database_url, min_size=1, max_size=10, timeout=10, command_timeout=30, init=_init_connection
            )
            log.info("db_pool_created")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
