"""asyncpg connection pool, created lazily so the API can start while Postgres is down."""

from __future__ import annotations

import asyncio
import hashlib
import json

import asyncpg

from .config import get_common_settings
from .logging import get_logger

log = get_logger(__name__)
_pool: asyncpg.Pool | None = None
_lock = asyncio.Lock()
STANDBY_POLL_S = 5
LOCK_CHECK_S = 15
LOST_LOCK_ERRORS = (OSError, TimeoutError, asyncpg.PostgresError, asyncpg.InterfaceError)


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


class Singleton:
    """Keeps one process of a kind running, using a Postgres advisory lock. A second one waits as a
    hot standby. Two brains would race on crash recovery, and two ingestion workers would double
    the polling that gets an IP banned by SACHET."""

    def __init__(self, name: str):
        self.name = name
        self.key = int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)
        self.conn: asyncpg.Connection | None = None

    async def _try(self) -> bool:
        self.conn = await asyncpg.connect(get_common_settings().database_url, timeout=10)
        if await self.conn.fetchval("SELECT pg_try_advisory_lock($1)", self.key):
            return True
        await self.conn.close()
        return False

    async def acquire(self) -> None:
        while not await self._try():
            log.warning("standby_waiting_for_lock", name=self.name)
            await asyncio.sleep(STANDBY_POLL_S)
        log.info("singleton_lock_held", name=self.name)

    async def keep(self) -> None:
        """Runs beside the worker. Postgres restarting drops the lock, so take it back; if another
        process got there first, this one must stop."""
        while True:
            await asyncio.sleep(LOCK_CHECK_S)
            try:
                await self.conn.fetchval("SELECT 1")
                continue
            except LOST_LOCK_ERRORS:
                log.warning("singleton_lock_lost", name=self.name)
            while True:
                try:
                    if await self._try():
                        break
                    raise RuntimeError(f"another {self.name} took over while Postgres was restarting")
                except LOST_LOCK_ERRORS:
                    await asyncio.sleep(STANDBY_POLL_S)

    async def release(self) -> None:
        if self.conn is not None:
            await self.conn.close()
