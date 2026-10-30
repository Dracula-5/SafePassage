"""Apply the contract baseline, then each owner's additive migrations.

Order: contracts/db/schema.sql (frozen baseline: types, all tables, views), then approved
contract patches contracts/db/patches/*.sql, then backend/migrations/*.sql by file name
(A: 1xx, B: 2xx). Each file runs once, in its own
transaction, recorded in schema_migrations under its repo-relative path with a checksum;
an applied file whose contents changed is an error (write a new migration instead).

Usage: python -m app.core.migrate   (run from backend/, DATABASE_URL set)
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import asyncpg

from .config import get_common_settings
from .logging import configure_logging, get_logger

log = get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE = REPO_ROOT / "contracts" / "db" / "schema.sql"
PATCHES_DIR = REPO_ROOT / "contracts" / "db" / "patches"
MIGRATIONS_DIR = REPO_ROOT / "backend" / "migrations"


class MigrationError(RuntimeError):
    pass


def discover(
    baseline: Path = BASELINE, migrations_dir: Path = MIGRATIONS_DIR, patches_dir: Path = PATCHES_DIR
) -> list[Path]:
    patches = sorted(patches_dir.glob("*.sql")) if patches_dir.exists() else []
    owner_files = sorted(migrations_dir.glob("*.sql")) if migrations_dir.exists() else []
    for path in owner_files:
        if path.name[:1] not in {"1", "2"}:
            raise MigrationError(f"{path.name}: owner migrations must start with 1 (A) or 2 (B)")
    return [baseline, *patches, *owner_files]


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _key(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:  # files outside the repo (tests)
        return str(path)


async def apply_all(conn: asyncpg.Connection, files: list[Path] | None = None) -> list[str]:
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               filename text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"""
    )
    applied = {r["filename"]: r["checksum"] for r in await conn.fetch("SELECT filename, checksum FROM schema_migrations")}
    newly_applied = []
    for path in files if files is not None else discover():
        key, digest = _key(path), checksum(path)
        if key in applied:
            if applied[key] != digest:
                raise MigrationError(f"{key} changed after it was applied")
            continue
        async with conn.transaction():
            await conn.execute(path.read_text())
            await conn.execute("INSERT INTO schema_migrations (filename, checksum) VALUES ($1, $2)", key, digest)
        log.info("migration_applied", file=key)
        newly_applied.append(key)
    return newly_applied


async def main() -> None:
    settings = get_common_settings()
    configure_logging(settings.log_level)
    conn = await asyncpg.connect(settings.database_url, timeout=10)
    try:
        applied = await apply_all(conn)
        log.info("migrations_done", applied=applied)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
