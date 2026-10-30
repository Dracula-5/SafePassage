#!/usr/bin/env python3
"""Gate self-audit (b): every changed file belongs to the given role.

Usage: python contracts/tools/check_ownership.py --role A [--base main]
Checks files changed since the merge-base with --base plus uncommitted/untracked files.
Exit code 1 if any file is owned by someone else or by nobody.

Ownership is decided by the most specific (longest) matching glob, so the G0 entry for
backend/app/api_senses/__init__.py wins over B's backend/app/api_senses/*.
"""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_rules() -> list[tuple[str, str]]:
    rules = []
    for line in (ROOT / "contracts" / "ownership.txt").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        role, glob = line.split(maxsplit=1)
        rules.append((role, glob))
    return rules


def owner_of(path: str, rules: list[tuple[str, str]]) -> str | None:
    matches = [(len(glob), role) for role, glob in rules if fnmatch.fnmatch(path, glob)]
    return max(matches)[1] if matches else None


def git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def changed_files(base: str) -> set[str]:
    files: set[str] = set()
    merge_base = git("merge-base", base, "HEAD")[0]
    files.update(git("diff", "--name-only", merge_base, "HEAD"))
    files.update(git("diff", "--name-only", "HEAD"))
    files.update(git("ls-files", "--others", "--exclude-standard"))
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True, choices=["A", "B", "G0"])
    parser.add_argument("--base", default="main")
    args = parser.parse_args()

    rules = load_rules()
    bad = []
    for path in sorted(changed_files(args.base)):
        owner = owner_of(path, rules)
        if owner != args.role:
            bad.append((path, owner or "nobody"))
    if bad:
        print(f"Ownership violations for role {args.role}:")
        for path, owner in bad:
            print(f"  {path}  (owner: {owner})")
        return 1
    print(f"OK: all changed files belong to role {args.role}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
