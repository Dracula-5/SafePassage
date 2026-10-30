"""Apply SQL migrations from contracts/db and backend/migrations in file-name order.

Order is 000 (contract types) -> 1xx (A) -> 2xx (B) -> 900 (contract views/FKs).
Each file runs once, in its own transaction, recorded in schema_migrations with a checksum;
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
MIGRATION_DIRS = (REPO_ROOT / "contracts" / "db", REPO_ROOT / "backend" / "migrations")


class MigrationError(RuntimeError):
    pass


def discover(dirs: tuple[Path, ...] = MIGRATION_DIRS) -> list[Path]:
    files: dict[str, Path] = {}
    for directory in dirs:
        for path in directory.glob("*.sql"):
            if path.name in files:
                raise MigrationError(f"duplicate migration name {path.name}: {files[path.name]} and {path}")
            files[path.name] = path
    return [files[name] for name in sorted(files)]


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def apply_all(conn: asyncpg.Connection, dirs: tuple[Path, ...] = MIGRATION_DIRS) -> list[str]:
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               filename text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"""
    )
    applied = {r["filename"]: r["checksum"] for r in await conn.fetch("SELECT filename, checksum FROM schema_migrations")}
    newly_applied = []
    for path in discover(dirs):
        digest = checksum(path)
        if path.name in applied:
            if applied[path.name] != digest:
                raise MigrationError(f"{path.name} changed after it was applied")
            continue
        async with conn.transaction():
            await conn.execute(path.read_text())
            await conn.execute("INSERT INTO schema_migrations (filename, checksum) VALUES ($1, $2)", path.name, digest)
        log.info("migration_applied", file=path.name)
        newly_applied.append(path.name)
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
