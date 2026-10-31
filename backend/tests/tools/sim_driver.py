"""TEST TOOL, never used at runtime: a simulated driver for exercising the closed loop.

Real phones are the data source. This exists because the road graph only covers Andhra Pradesh, so
nobody outside it can test with real GPS. It talks to the public API exactly like the Driver PWA,
and every vehicle it creates is named TEST-SIM-GPS so a simulated fix can never pass as a real one.

  python -m tests.tools.sim_driver --lon 80.75 --lat 16.52                drive every assignment
  ... --off-route-after-stops 1 --off-route-to 80.72,16.62                leave the route after a stop
  ... --silent-after-stops 1                                              stop reporting (driver offline)
"""

from __future__ import annotations

import argparse
import math
import os
import time
from datetime import UTC, datetime

import httpx

LABEL_PREFIX = "TEST-SIM-GPS"
REPORTED_SPEED_MPS = 15  # a plausible truck speed; the test may move far faster to save time


def metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    dx = (b[0] - a[0]) * 111_320 * math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot(dx, (b[1] - a[1]) * 110_540)


def advance(line: list[tuple[float, float]], at: int, frac: float, step_m: float) -> tuple[int, float, tuple[float, float]]:
    """Move `step_m` along the polyline from segment `at`, `frac` of the way through it."""
    while at < len(line) - 1:
        a, b = line[at], line[at + 1]
        length = metres(a, b) or 0.01
        left = (1 - frac) * length
        if step_m < left:
            frac += step_m / length
            return at, frac, (a[0] + (b[0] - a[0]) * frac, a[1] + (b[1] - a[1]) * frac)
        step_m -= left
        at, frac = at + 1, 0.0
    return at, 1.0, line[-1]


class Driver:
    def __init__(self, http: httpx.Client, args: argparse.Namespace):
        self.http, self.args = http, args
        self.position = (args.lon, args.lat)
        self.route_id, self.line, self.at, self.frac = None, [], 0, 0.0
        body = {"label": f"{LABEL_PREFIX}-{args.name}", "driver_language": args.language, "capacity_kg": args.capacity_kg}
        registered = http.post("/api/vehicles", json=body, headers={"X-Enrolment-Code": os.environ.get("ENROLMENT_CODE", "")})
        registered.raise_for_status()
        self.vehicle_id = registered.json()["vehicle"]["id"]
        self.token = registered.json()["device_token"]
        print(f"registered {body['label']} {self.vehicle_id}", flush=True)

    def report(self) -> None:
        fix = {"lon": self.position[0], "lat": self.position[1], "accuracy_m": 15.0,
               "recorded_at": datetime.now(UTC).isoformat(), "speed_mps": REPORTED_SPEED_MPS}  # fmt: skip
        response = self.http.post("/api/telemetry", headers={"X-Device-Token": self.token},
                                  json={"vehicle_id": self.vehicle_id, "fixes": [fix]})  # fmt: skip
        response.raise_for_status()

    def follow_assignment(self) -> int:
        """Returns completed stops, or -1 with no assignment."""
        response = self.http.get(f"/api/vehicles/{self.vehicle_id}/assignment", headers={"X-Device-Token": self.token})
        if response.status_code == 404:
            return -1
        response.raise_for_status()
        route = response.json()["route"]
        if route["id"] != self.route_id:
            print(f"new route {route['id']}: {len(route['stops'])} stops, {route['distance_m'] / 1000:.1f} km", flush=True)
            self.route_id, self.line, self.at, self.frac = route["id"], [tuple(c) for c in route["geometry"]["coordinates"]], 0, 0.0
        return route["completed_stops"]

    def run(self) -> None:
        a = self.args
        off_route = tuple(float(v) for v in a.off_route_to.split(",")) if a.off_route_to else None
        left_route: str | None = None  # the route this driver abandoned, until a replacement arrives
        has_left = False
        while True:
            try:
                self.report()
                done = self.follow_assignment()
            except httpx.HTTPError as exc:  # the API restarted: carry on, like a phone in a dead zone
                print(f"api unavailable: {exc}", flush=True)
                time.sleep(a.tick_s)
                continue
            if a.silent_after_stops is not None and done >= a.silent_after_stops:
                print("going silent: the dashboard should mark this vehicle offline", flush=True)
                return
            if left_route == self.route_id:
                pass  # parked off route, waiting for the Replanner
            elif off_route and not has_left and a.off_route_after_stops is not None and done >= a.off_route_after_stops:
                print(f"leaving the route for {off_route}", flush=True)
                self.position, left_route, has_left = off_route, self.route_id, True
            elif done >= 0:
                self.at, self.frac, self.position = advance(self.line, self.at, self.frac, a.speed_mps * a.tick_s)
            time.sleep(a.tick_s)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--name", default="1")
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--capacity-kg", type=float, default=6000)
    parser.add_argument("--language", default="te")
    parser.add_argument("--tick-s", type=float, default=5, help="seconds between fixes")
    parser.add_argument("--speed-mps", type=float, default=200, help="faster than a truck, to shorten a test")
    parser.add_argument("--off-route-after-stops", type=int)
    parser.add_argument("--off-route-to", help="lon,lat")
    parser.add_argument("--silent-after-stops", type=int)
    args = parser.parse_args()
    with httpx.Client(base_url=args.api, timeout=15) as http:
        Driver(http, args).run()


if __name__ == "__main__":
    main()
