"""Test configuration for Instance A. Integration tests use real services from the env."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

# Unit tests never connect; these only satisfy CommonSettings' required fields.
# Integration tests read TEST_DATABASE_URL / TEST_REDIS_URL and skip when they are unset.
os.environ.setdefault("DATABASE_URL", "postgresql://unused@127.0.0.1:1/unused")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:1/0")

sys.path.insert(0, str(REPO_ROOT / "contracts" / "tools"))


@pytest.fixture(scope="session")
def event_loop_policy():
    """psycopg's async mode (the LangGraph checkpointer) needs a selector loop on Windows."""
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.get_event_loop_policy()
