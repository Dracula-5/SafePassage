"""Test setup for Instance B. Integration tests use TEST_DATABASE_URL / TEST_REDIS_URL and skip without them."""

import os
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

FIXTURES = Path(__file__).parent / "fixtures"

os.environ.setdefault("DATABASE_URL", "postgresql://unused@127.0.0.1:1/unused")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:1/0")
os.environ.setdefault("REGION_BBOX", "76.6,12.5,84.9,20.1")

ADMIN_URL = os.environ.get("TEST_DATABASE_URL")
REDIS_URL = os.environ.get("TEST_REDIS_URL")


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class NoKnownRows:
    """Stand-in for the pool in parser-level tests: nothing has been ingested yet."""

    async def fetch(self, *args, **kwargs):
        return []


@pytest.fixture
async def scratch_db_url():
    if not ADMIN_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    from app.core.migrate import apply_all

    admin = await asyncpg.connect(ADMIN_URL, timeout=10)
    name = f"rr_senses_{uuid4().hex[:8]}"
    await admin.execute(f'CREATE DATABASE "{name}"')
    url = f"{ADMIN_URL.rpartition('/')[0]}/{name}"
    conn = await asyncpg.connect(url)
    await apply_all(conn)
    await conn.close()
    try:
        yield url
    finally:
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


@pytest.fixture
async def live_env(scratch_db_url, monkeypatch):
    """Points app.core (pool, redis) at the scratch DB and the test Redis."""
    if not REDIS_URL:
        pytest.skip("TEST_REDIS_URL not set")
    from app.core import bus, db
    from app.core.config import get_common_settings

    monkeypatch.setenv("DATABASE_URL", scratch_db_url)
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    get_common_settings.cache_clear()
    await db.close_pool()
    await bus.close_redis()
    yield await db.get_pool()
    await db.close_pool()
    await bus.close_redis()
    get_common_settings.cache_clear()
