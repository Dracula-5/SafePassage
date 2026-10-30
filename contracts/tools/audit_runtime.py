#!/usr/bin/env python3
"""Gate self-audit (a): scan runtime code for mock data, placeholders and hardcoded coordinates.

Usage: python contracts/tools/audit_runtime.py [paths...]
Defaults to backend/app, frontend, driver-pwa. Skips tests/, node_modules/, build output.
A line can be allowed with a trailing comment containing `audit: allow <reason>`; every
allow is printed so reviewers see them.
Exit code 1 on any finding.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATHS = ["backend/app", "frontend", "driver-pwa"]
SKIP_PARTS = {"tests", "node_modules", ".next", "dist", "__pycache__", ".venv", "public"}
EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".sql", ".json", ".yaml", ".yml"}

PATTERNS = {
    "mock-word": re.compile(r"\b(mock|dummy|fake|lorem|placeholder data|sample_data|hardcoded)\b", re.I),
    # Two decimal numbers that look like a lat/lon or lon/lat pair (>= 3 decimals each).
    "coordinate-pair": re.compile(r"-?\d{1,3}\.\d{3,}\s*,\s*-?\d{1,3}\.\d{3,}"),
    "geojson-literal": re.compile(r"[\"']coordinates[\"']\s*:\s*\["),
}
ALLOW = re.compile(r"audit:\s*allow\s+\S+")


def scan(paths: list[str]) -> int:
    findings = 0
    for base in paths:
        root = ROOT / base
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in EXTENSIONS or SKIP_PARTS & set(path.relative_to(ROOT).parts):
                continue
            for lineno, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                for name, pattern in PATTERNS.items():
                    if not pattern.search(line):
                        continue
                    rel = path.relative_to(ROOT)
                    if ALLOW.search(line):
                        print(f"ALLOWED {rel}:{lineno} [{name}] {line.strip()}")
                    else:
                        print(f"FINDING {rel}:{lineno} [{name}] {line.strip()}")
                        findings += 1
    print(f"{findings} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(scan(sys.argv[1:] or DEFAULT_PATHS))
