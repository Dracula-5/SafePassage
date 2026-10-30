"""Runs the Driver PWA's offline-queue tests so one `pytest` covers them too."""

import shutil
import subprocess
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parents[3] / "driver-pwa" / "tests"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_offline_queue():
    done = subprocess.run(["node", "--test", str(TESTS / "queue.test.js")], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
