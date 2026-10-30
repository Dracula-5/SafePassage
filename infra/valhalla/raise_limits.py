#!/usr/bin/env python3
"""Raise Valhalla's exclude_polygons limits so real hazard zones are accepted.

The defaults (10 km total perimeter, 100 vertices) reject even one mandal-sized polygon.
"""

import json
import os
import sys
from pathlib import Path

config = Path(os.environ.get("DATA_DIR", Path.home() / "reliefroute-data")) / "valhalla" / "valhalla.json"
if not config.exists():
    sys.exit(f"{config} not found; start the valhalla service once first")
data = json.loads(config.read_text())
limits = data["service_limits"]
limits["max_exclude_polygons_length"] = 5_000_000  # metres
limits["max_exclude_polygons_vertices"] = 10_000
config.write_text(json.dumps(data, indent=2))
print({k: v for k, v in limits.items() if "exclude" in k})
