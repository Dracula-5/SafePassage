"""Apply the contract baseline, then each owner's additive migrations.

Order: contracts/db/schema.sql (frozen baseline: types, all tables, views), then approved
contract patches contracts/db/patches/*.sql, then backend/migrations/*.sql by file name
(A: 1xx, B: 2xx). Each file runs once, in its own
transaction, recorded in schema_migrations under its repo-relative path with a checksum;
an applied file whose contents changed is an error (write a new migration instead).

Usage: python -m app.core.migrate   (run from backend/, DATABASE_URL set)

Migrations need a superuser (CREATE EXTENSION); the running services should not have one. With
APP_DB_ROLE and APP_DB_PASSWORD set, this also creates that role with table access and nothing
more, so a hole in the API can reach the data but not the server underneath (COPY ... TO PROGRAM).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

import asyncpg

from .config import get_common_settings
from .logging import configure_logging, get_logger

log = get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE = REPO_ROOT / "contracts" / "db" / "schema.sql"
PATCHES_DIR = REPO_ROOT / "contracts" / "db" / "patches"
MIGRATIONS_DIR = REPO_ROOT / "backend" / "migrations"
MIGRATION_LOCK = 7_401_002


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
        return path.relative_to(REPO_ROOT).as_posix()  # the same on every OS: dumps move between machines
    except ValueError:  # files outside the repo (tests)
        return str(path)


async def apply_all(conn: asyncpg.Connection, files: list[Path] | None = None) -> list[str]:
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               filename text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"""
    )
    # Databases first migrated on Windows recorded backslashes. Left alone, Linux would re-run everything.
    await conn.execute(r"UPDATE schema_migrations SET filename = replace(filename, '\', '/') WHERE filename LIKE 'contracts\\%' OR filename LIKE 'backend\\%'")
    applied = {r["filename"]: r["checksum"] for r in await conn.fetch("SELECT filename, checksum FROM schema_migrations")}
    newly_applied = []
    for path in files if files is not None else discover():
        key, digest = _key(path), checksum(path)
        if key in applied:
            if applied[key] != digest:
                raise MigrationError(f"{key} changed after it was applied")
            continue
        async with conn.transaction():
            await conn.execute(path.read_text(encoding="utf-8"))
            await conn.execute("INSERT INTO schema_migrations (filename, checksum) VALUES ($1, $2)", key, digest)
        log.info("migration_applied", file=key)
        newly_applied.append(key)
    return newly_applied


async def grant_app_role(conn: asyncpg.Connection, role: str, password: str) -> None:
    """Safe to repeat on every deploy: it also rotates the password and covers new tables."""
    name = await conn.fetchval("SELECT quote_ident($1)", role)
    secret = await conn.fetchval("SELECT quote_literal($1)", password)
    verb = "ALTER" if await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role) else "CREATE"
    await conn.execute(f"{verb} ROLE {name} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {secret}")
    # CREATE on the schema is for the LangGraph checkpointer, which sets up its own tables.
    await conn.execute(f"""
        GRANT USAGE, CREATE ON SCHEMA public TO {name};
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {name};
        GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {name};
        ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {name};
        ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {name};
    """)
    log.info("app_role_ready", role=role)


async def main() -> None:
    settings = get_common_settings()
    configure_logging(settings.log_level)
    conn = await asyncpg.connect(settings.database_url, timeout=10)
    try:
        await conn.execute(f"SELECT pg_advisory_lock({MIGRATION_LOCK})")  # two deploys at once take turns
        applied = await apply_all(conn)
        log.info("migrations_done", applied=applied)
        role, password = os.environ.get("APP_DB_ROLE"), os.environ.get("APP_DB_PASSWORD")
        if role and password:
            await grant_app_role(conn, role, password)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
