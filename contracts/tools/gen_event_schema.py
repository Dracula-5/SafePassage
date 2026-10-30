#!/usr/bin/env python3
"""Regenerate contracts/events.schema.json from the Pydantic event models."""

import json
from pathlib import Path

from reliefroute_contracts.events import event_json_schema

OUT = Path(__file__).resolve().parents[1] / "events.schema.json"


def render() -> str:
    return json.dumps(event_json_schema(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    OUT.write_text(render())
    print(f"wrote {OUT}")
