#!/usr/bin/env python3
"""DEMO SEED: drinking-water stock for the demo, created at the project owner's request.

There is no public source of packaged drinking-water stock in Andhra Pradesh. Rice and pulses come
from the live AePDS government feed; water does not. This script registers clearly labelled demo
suppliers at real OSM storage sites and checks in stock through the normal supplier portal API,
so the data path is identical to a real check-in.

Every seeded supplier is named "DEMO SEED (not real stock): ..." and is left UNVERIFIED, so the
Brain's approval gate always flags it, and the name appears on dashboards, briefs and PO PDFs.
This is NOT runtime code and must not be run for anything but a demo.

Usage: python infra/demo/seed_demo_water.py --api http://localhost:8000 --facility <id>:<litres> [...]
"""

import argparse
import json
import urllib.request

LABEL = "DEMO SEED (not real stock)"


def post(api: str, path: str, body: dict, headers: dict | None = None) -> dict:
    req = urllib.request.Request(f"{api}{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True)
    parser.add_argument("--facility", action="append", required=True, help="facility_id:litres")
    args = parser.parse_args()
    for spec in args.facility:
        facility_id, litres = spec.split(":")
        reg = post(args.api, "/portal/api/register",
                   {"name": f"{LABEL}: drinking water", "preferred_language": "te", "facility_id": facility_id})
        levels = post(args.api, "/portal/api/stock",
                      {"lines": [{"item_code": "WATER_L", "quantity": float(litres), "unit_weight_kg": 1.0}]},
                      {"X-Supplier-Token": reg["supplier_token"]})
        print(f"seeded {litres} L at facility {facility_id} as supplier {reg['supplier_id']} (unverified): {levels}")


if __name__ == "__main__":
    main()
