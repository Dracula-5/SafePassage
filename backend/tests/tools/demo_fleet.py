"""TEST TOOL for presentations: a fleet of simulated drivers across Andhra Pradesh.

Runs in its own container under compose profile `demo`, and is not part of the production image. Real
phones are the data source, but nobody can show a drill without trucks near it, and the road graph only
covers Andhra Pradesh. So this keeps trucks online in every district and sends them where they are needed:

  every drill           at least DRILL_TRUCKS wait around it, clear of the zone
  the worst real alert  FOCUS_TRUCKS
  other severe alerts   ALERT_TRUCKS

While a drill or severe alert is active, it also asks for an evacuation every 10 to 15 minutes, with a
made-up number of people. The nearest free truck collects them and takes them to safety.

Trucks wait at real fuel stations, godowns and schools, and drive approved routes along the real road
line, faster than a truck so a delivery finishes during a presentation. Like sim_driver.py it uses only
the API a phone uses. The drivers and plates are made up, every label starts with TEST-SIM-GPS, and they
have no phone numbers. Water depots are simulated too, since water has no public stock source.

  python -m tests.tools.demo_fleet       API_URL, OPERATOR_TOKEN; optional DEMO_SPEED_MPS
"""

from __future__ import annotations

import asyncio
import math
import os
import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from shapely import Point, unary_union
from shapely.geometry import shape

from tests.tools.sim_driver import REPORTED_SPEED_MPS, advance, metres

TICK_S = 5
DEFAULT_SPEED_MPS = 1200  # a 300 km route, normal for a plan with several stops, takes about 5 minutes
IDLE_FIX_S = 30          # the dashboard calls a truck offline after 120 s of silence
TRUCKS_PER_TOWN = 3
DRILL_TRUCKS = 10
FOCUS_TRUCKS = 10
ALERT_TRUCKS = 2
NEAR_DEG = 0.25           # about 25 km: a truck this close to a zone already counts as being there
CLEAR_DEG = 0.02          # about 2 km: wider than any routing margin, so a truck can drive away from the zone
SEARCH_DEG = 0.2
STOP_NEAR_M = 250
OFF_ROUTE_DEGS = (0.02, 0.05, 0.1, 0.2)
OFF_ROUTE_CLEAR_M = 1000  # twice the Route Monitor's threshold
EVACUATION_EVERY_S = (600, 900)
FIRST_EVACUATION_S = 120  # soon after starting, so a rehearsal doesn't wait 10 minutes to see one
EVACUEES = (20, 60)
RESTOCK_EVERY_S = 300
ANSWER_TICKS = 2          # a simulated driver takes a moment to answer, so the request is seen going out
GLIDE_TICKS = 3          # how long a truck takes to reach its post: quick, because a plan starts at once
DEPOT_WATER_L = 60_000
SPOT_KINDS = "fuel,godown,warehouse,school"
ROAD_CLOSING_TYPES = ("flood", "cyclone", "landslide", "tsunami", "earthquake", "wildfire")  # app/routing/repository.py
SEVERE = ("severe", "extreme")
PLATE_SERIES = "ABCDEGHJKLMNPRSUVWXYZ"
TRUCK_KG = 8000           # all alike, so any truck can take any part of an order (planner.part_size_kg)
LANGUAGES = ("te", "en", "en")  # Gemini's free tier can't translate a whole fleet's instructions at once
DRIVERS = (
    "Ramesh Naidu", "Srinivasa Rao", "Lakshmi Prasad", "Venkateswarlu", "Nagarjuna Reddy", "Koteswara Rao", "Satyanarayana",
    "Suresh Chowdary", "Ravi Kumar", "Prasad Varma", "Hanumantha Rao", "Mallikarjuna Reddy", "Durga Prasad", "Sambasiva Rao",
    "Anjaneyulu", "Chandra Sekhar", "Lakshmana Rao", "Bhaskar Reddy", "Gopi Krishna", "Narasimha Rao", "Madhava Rao", "Rambabu",
    "Appa Rao", "Yesu Babu", "Ismail Basha", "Khaja Mohiddin", "Peddaiah", "Sivaji", "Trinadh", "Vijay Kumar", "Jagadeesh",
    "Kondala Rao", "Pothuraju", "Ramana Murthy", "Eswara Rao", "Tirupathi Naidu", "Balaji", "Subba Reddy", "Obulesu",
    "Mastan Vali", "Padmavathi", "Rajeswari",
)  # fmt: skip

LonLat = tuple[float, float]
TOWN_CENTRES: dict[str, LonLat] = {
    "Srikakulam": (83.8938, 18.2949), "Vizianagaram": (83.3956, 18.1067), "Visakhapatnam": (83.2185, 17.6868),
    "Kakinada": (82.2475, 16.9891), "Rajamahendravaram": (81.8040, 17.0005), "Eluru": (81.0950, 16.7107),
    "Vijayawada": (80.6480, 16.5062), "Guntur": (80.4365, 16.3067), "Ongole": (80.0499, 15.5057), "Nellore": (79.9865, 14.4426),
    "Tirupati": (79.4192, 13.6288), "Kadapa": (78.8242, 14.4673), "Kurnool": (78.0373, 15.8281), "Anantapur": (77.6006, 14.6819),
}  # fmt: skip


def legs_to_stops(line: list[LonLat], stops: list[LonLat]) -> list[list[LonLat]]:
    """The route cut at each stop. A truck faster than a real one would jump past a stop between two
    fixes, so each leg ends exactly at its stop."""
    legs, start = [], 0
    for stop in stops:
        close = next((i for i in range(start, len(line)) if metres(line[i], stop) <= STOP_NEAR_M), None)
        end = close if close is not None else min(range(start, len(line)), key=lambda i: metres(line[i], stop))
        legs.append([*line[start : end + 1], stop])
        start = end
    return legs


def away_from(line: list[LonLat], position: LonLat) -> LonLat:
    """Somewhere the route doesn't go. Routes double back, so a fixed step can land right beside one,
    and the Route Monitor would rightly see nothing wrong."""
    spot = position
    for reach in OFF_ROUTE_DEGS:
        for angle in range(0, 360, 45):
            spot = (position[0] + reach * math.cos(math.radians(angle)), position[1] + reach * math.sin(math.radians(angle)))
            if all(metres(spot, point) > OFF_ROUTE_CLEAR_M for point in line):
                return spot
    return spot


def glide_path(start: LonLat, end: LonLat, ticks: int = GLIDE_TICKS) -> list[LonLat]:
    return [(start[0] + (end[0] - start[0]) * i / ticks, start[1] + (end[1] - start[1]) * i / ticks) for i in range(1, ticks + 1)]


def closes_roads(hazard: dict) -> bool:
    return hazard["is_drill"] or (hazard["hazard_type"] in ROAD_CLOSING_TYPES and hazard["severity"] in SEVERE)


def worth_a_visit(features: list[dict]) -> list[dict]:
    """Drills first, then real severe alerts, worst first (the API's own order)."""
    wanted = [f for f in features if f["properties"]["is_drill"] or f["properties"]["severity"] in SEVERE]
    return sorted(wanted, key=lambda f: not f["properties"]["is_drill"])


@dataclass
class Truck:
    id: str
    label: str
    token: str
    person: bool = False       # a signed-in driver is presenting this one, so it gets the post nearest the zone
    position: LonLat | None = None
    mode: str = "normal"
    route_id: str | None = None
    line: list[LonLat] = field(default_factory=list)
    legs: list[list[LonLat]] = field(default_factory=list)
    at: int = 0
    frac: float = 0.0
    pause: bool = False
    glide: list[LonLat] = field(default_factory=list)
    post: str | None = None    # the hazard it was sent to
    wandered: bool = False
    last_fix: float = 0.0
    request_waited: int = 0
    request_done: str | None = None  # the accepted request it has already gone to

    @property
    def free(self) -> bool:
        return self.position is not None and self.mode == "normal" and self.route_id is None

    def step(self, step_m: float) -> bool:
        """Moves the truck for one tick. True if it should report its position now."""
        if self.mode == "gps_lost" or self.position is None:
            return False
        if self.mode == "off_route":
            if not self.wandered:
                self.position, self.wandered = away_from(self.line, self.position), True
            return True
        if self.pause:  # a tick at the stop, so the screens show it arriving
            self.pause = False
            return True
        if self.legs:
            self.at, self.frac, self.position = advance(self.legs[0], self.at, self.frac, step_m)
            if self.frac >= 1.0:
                self.legs.pop(0)
                self.at, self.frac, self.pause = 0, 0.0, True
            return True
        if self.glide:
            self.position = self.glide.pop(0)
            return True
        return time.monotonic() - self.last_fix >= IDLE_FIX_S

    def distance_to(self, zone) -> float:
        return zone.distance(Point(self.position))


class Fleet:
    def __init__(self, http: httpx.AsyncClient, speed_mps: float):
        self.http, self.speed_mps = http, speed_mps
        self.trucks: dict[str, Truck] = {}
        self.towns: list[dict] = []
        self.handled: set[str] = set()   # hazards already looked after
        self.focused = False
        self.home_spots: dict[str, list[LonLat]] = {}
        self.hazards_seen = ""
        self.features: list[dict] = []
        self.next_evacuation = time.monotonic() + FIRST_EVACUATION_S
        self.next_restock = time.monotonic() + RESTOCK_EVERY_S

    async def _get(self, path: str, **params) -> httpx.Response:
        response = await self.http.get(path, params=params)
        response.raise_for_status()
        return response

    async def _post(self, path: str, body: dict, **kwargs) -> httpx.Response:
        response = await self.http.post(path, json=body, **kwargs)
        response.raise_for_status()
        return response

    async def spots(self, bounds: tuple[float, float, float, float], kinds: str = SPOT_KINDS) -> list[LonLat]:
        x0, y0, x1, y1 = bounds
        box = f"{x0 - SEARCH_DEG:.4f},{y0 - SEARCH_DEG:.4f},{x1 + SEARCH_DEG:.4f},{y1 + SEARCH_DEG:.4f}"
        features = (await self._get("/api/facilities", bbox=box, kind=kinds)).json()["features"]
        return [tuple(f["geometry"]["coordinates"]) for f in features]

    async def home_spot(self, name: str) -> LonLat:
        lon, lat = TOWN_CENTRES[name]
        if name not in self.home_spots:
            found = await self.spots((lon, lat, lon, lat), "fuel,godown,warehouse")
            found.sort(key=lambda p: metres(p, (lon, lat)))
            self.home_spots[name] = found[:TRUCKS_PER_TOWN * 3] or [(lon, lat)]
        return self.home_spots[name].pop(0) if len(self.home_spots[name]) > 1 else self.home_spots[name][0]

    async def seed(self) -> None:
        """Safe to repeat: only what is missing gets created."""
        self.towns = (await self._get("/api/demo")).json()["towns"]
        have = {row["label"] for row in (await self._get("/api/demo/fleet")).json()}
        names = iter(DRIVERS)
        for t, town in enumerate(self.towns):
            for n in range(TRUCKS_PER_TOWN):
                rng = random.Random(f"{town['name']}{n}")  # the same plate every time
                plate = f"AP {town['rto']} T{rng.choice(PLATE_SERIES)} {rng.randint(1000, 9999)}"
                name = next(names)
                if any(label.endswith(plate) for label in have):
                    continue
                await self._post("/api/demo/drivers", {
                    "driver_name": name, "plate": plate, "capacity_kg": TRUCK_KG,
                    "driver_language": LANGUAGES[(t + n) % len(LANGUAGES)], "town": town["name"]})  # fmt: skip
                print(f"added {plate} {name} at {town['name']}", flush=True)
        await self.seed_depots()

    async def seed_depots(self) -> None:
        for town in self.towns:
            site = f"TEST-SIM water depot {town['name']}"
            if any(f["name"] == site for f in (await self._get("/portal/api/facilities", q=site)).json()):
                continue  # one search per depot: the search stops at 20 results, and closed depots fill them
            lon, lat = await self.home_spot(town["name"])
            depot = (await self._post("/portal/api/register", {"name": site, "site_name": site, "lon": lon, "lat": lat})).json()
            await self._post("/portal/api/stock", {"lines": [{"item_code": "WATER_L", "quantity": DEPOT_WATER_L, "unit_weight_kg": 1}]},
                             headers={"X-Supplier-Token": depot["supplier_token"]})  # fmt: skip
            await self._post(f"/api/suppliers/{depot['supplier_id']}/verify", {"verified": True})
            print(f"added {site}", flush=True)

    async def sync(self) -> None:
        """Picks up new trucks, removed trucks, new routes and what the duty officer pressed."""
        rows = (await self._get("/api/demo/fleet")).json()
        for row in rows:
            truck = self.trucks.get(row["vehicle_id"])
            if row["removed"]:
                self.trucks.pop(row["vehicle_id"], None)
                continue
            if truck is None:
                last = (row["last_lon"], row["last_lat"]) if row["last_lon"] is not None else None
                in_region = last and 76 < last[0] < 85 and 12 < last[1] < 21  # a person's old phone fix may be anywhere
                position = last if in_region else await self.home_spot(row["town"])
                truck = Truck(id=row["vehicle_id"], label=row["label"], token=row["device_token"], person=row["has_person"], position=position)
                self.trucks[truck.id] = truck
            truck.token, truck.person = row["device_token"], row["has_person"]
            changed = row["route_id"] != truck.route_id
            mode = row["mode"]
            if mode == "off_route" and (row["route_id"] is None or (changed and truck.mode == "off_route")):
                mode = "normal"  # the Replanner has dealt with it, or there was no route to leave
                await self._post(f"/api/demo/drivers/{truck.id}/mode", {"mode": "normal"})
            if mode == "normal":
                truck.wandered = False
            truck.mode = mode
            if changed:
                await self.follow(truck, row["route_id"])
            await self.answer_request(truck, row)

    async def follow(self, truck: Truck, route_id: str | None) -> None:
        truck.route_id, truck.line, truck.legs, truck.at, truck.frac, truck.glide = route_id, [], [], 0, 0.0, []
        if route_id is None:
            return
        response = await self.http.get(f"/api/vehicles/{truck.id}/assignment")
        if response.status_code == 404:  # finished between the two calls
            truck.route_id = None
            return
        response.raise_for_status()
        route = response.json()["route"]
        line = [tuple(c) for c in route["geometry"]["coordinates"]]
        nearest = min(range(len(line)), key=lambda i: metres(line[i], truck.position)) if truck.position else 0
        stops = [(s["lon"], s["lat"]) for s in route["stops"][route["completed_stops"]:]]
        truck.line, truck.legs = line, legs_to_stops(line[nearest:], stops)
        print(f"{truck.label}: following route {route_id}, {len(stops)} stops", flush=True)

    def closed_zones(self):
        return unary_union([shape(f["geometry"]) for f in self.features if closes_roads(f["properties"])])

    async def open_spots(self, zone, closed) -> list[LonLat]:
        found = [p for p in await self.spots(zone.bounds) if closed.is_empty or closed.distance(Point(p)) > CLEAR_DEG]
        return sorted(found, key=lambda p: zone.distance(Point(p)))

    async def answer_request(self, truck: Truck, row: dict) -> None:
        """Answers for a simulated driver, and takes any accepted truck to the place. A person answers
        on their own screen, but their simulated truck still has to be driven there."""
        if row["request_status"] == "sent" and not truck.person:
            truck.request_waited += 1
            if truck.request_waited >= ANSWER_TICKS:
                truck.request_waited = 0
                await self._post(f"/api/vehicles/{truck.id}/request/answer", {"accept": True})
        elif row["request_status"] == "accepted" and row["request_id"] != truck.request_done and truck.free:
            truck.request_done = row["request_id"]
            hazard = next((f for f in self.features if f["id"] == row["request_hazard_id"]), None)
            spots = await self.open_spots(shape(hazard["geometry"]), self.closed_zones()) if hazard else []
            if spots:
                truck.glide, truck.post = glide_path(truck.position, spots[0]), hazard["id"]

    async def station(self) -> None:
        changed = (await self._get("/api/status")).json()["hazards_changed"]
        if changed == self.hazards_seen:
            return
        self.hazards_seen = changed
        self.features = (await self._get("/api/hazards")).json()["features"]
        closed = self.closed_zones()
        for hazard in worth_a_visit(self.features):
            if hazard["id"] in self.handled:
                continue
            self.handled.add(hazard["id"])
            drill = hazard["properties"]["is_drill"]
            want = DRILL_TRUCKS if drill else ALERT_TRUCKS if self.focused else FOCUS_TRUCKS
            sent = await self.send_trucks(shape(hazard["geometry"]), closed, want, hazard["id"], drill)
            if sent is not None and not drill:
                self.focused = True  # an alert out at sea has nowhere to wait, so it can't be the focus
            print(f"hazard {hazard['properties']['headline'][:60]!r}: sent {sent} truck(s)", flush=True)

    async def send_trucks(self, zone, closed, want: int, hazard_id: str, drill: bool) -> int | None:
        usable = [t for t in self.trucks.values() if t.free]
        people = [t for t in usable if t.person]  # a real alert must not pull a presenter's truck away from their drill
        fleet = [t for t in usable if not t.person]
        stuck = [t for t in fleet if closed.contains(Point(t.position))]
        ready = [t for t in fleet if t not in stuck and t.distance_to(zone) <= NEAR_DEG]
        others = sorted((t for t in fleet if t not in stuck and t not in ready and (drill or t.post is None)),
                        key=lambda t: t.distance_to(zone))  # fmt: skip
        going = [*(people if drill else []), *stuck, *others[: max(want - len(ready), 0)]]
        if not going:
            return 0
        open_spots = await self.open_spots(zone, closed)
        if not open_spots:
            return None
        step = max(1, len(open_spots) // (len(going) * 3))  # spread out instead of queueing at one pump
        for truck, spot in zip(going, open_spots[::step], strict=False):
            # The planner reads positions within seconds, and it can't route a truck that is still inside the zone.
            urgent = truck.person or truck in stuck
            truck.glide, truck.post = glide_path(truck.position, spot, 1 if urgent else GLIDE_TICKS), hazard_id
        return len(going)

    async def request_evacuation(self) -> None:
        if time.monotonic() < self.next_evacuation:
            return
        zones = worth_a_visit(self.features)
        if not zones:
            return  # nothing is active: ask as soon as something is
        self.next_evacuation = time.monotonic() + random.uniform(*EVACUATION_EVERY_S)
        for hazard in zones:  # a zone with no settlement inside is refused, so try the next
            body = {"hazard_id": hazard["id"], "people": random.randint(*EVACUEES), "simulated": True}
            response = await self.http.post("/api/evacuations", json=body)
            if response.status_code == 201:
                print(f"evacuation requested: {response.json()['people']} people at {response.json()['settlement']}", flush=True)
                return

    async def report(self, truck: Truck) -> None:
        response = await self.http.post(
            "/api/telemetry", headers={"X-Device-Token": truck.token},
            json={"vehicle_id": truck.id, "fixes": [{
                "lon": truck.position[0], "lat": truck.position[1], "accuracy_m": 15.0,
                "recorded_at": datetime.now(UTC).isoformat(),
                "speed_mps": REPORTED_SPEED_MPS if truck.legs or truck.glide else 0}]})  # fmt: skip
        if response.status_code == 401:  # taken off the fleet a moment ago
            self.trucks.pop(truck.id, None)
            return
        response.raise_for_status()
        truck.last_fix = time.monotonic()

    async def tick(self) -> None:
        await self.sync()
        await self.station()
        await self.request_evacuation()
        if time.monotonic() >= self.next_restock:
            self.next_restock = time.monotonic() + RESTOCK_EVERY_S
            await self._post("/api/demo/restock", {})
        reporting = [t for t in self.trucks.values() if t.step(self.speed_mps * TICK_S)]
        await asyncio.gather(*(self.report(t) for t in reporting))


async def wait_for_places(fleet: Fleet) -> None:
    """A new database fills from the live sources: there is nowhere to park until facilities have loaded."""
    while True:
        try:
            status = (await fleet.http.get("/api/demo")).json()
            if status.get("enabled") and (await fleet.spots((80.6, 16.5, 80.7, 16.6))):
                return
        except (httpx.HTTPError, ValueError) as exc:
            print(f"waiting for the API: {exc}", flush=True)
        await asyncio.sleep(TICK_S)


async def main() -> None:
    speed = float(os.environ.get("DEMO_SPEED_MPS") or DEFAULT_SPEED_MPS)
    headers = {"X-Operator-Token": os.environ["OPERATOR_TOKEN"]}
    async with httpx.AsyncClient(base_url=os.environ.get("API_URL", "http://api:8000"), headers=headers, timeout=20) as http:
        fleet = Fleet(http, speed)
        await wait_for_places(fleet)
        while True:
            try:
                await fleet.seed()
                break
            except httpx.HTTPError as exc:
                print(f"could not create the fleet yet: {exc}", flush=True)
                await asyncio.sleep(TICK_S)
        print(f"demo fleet running: {len(fleet.towns)} towns, {speed:.0f} m/s", flush=True)
        while True:
            started = time.monotonic()
            try:
                await fleet.tick()
            except httpx.HTTPError as exc:  # the API restarted: carry on, like a phone in a dead zone
                print(f"api unavailable: {exc}", flush=True)
            await asyncio.sleep(max(0.0, TICK_S - (time.monotonic() - started)))


if __name__ == "__main__":
    asyncio.run(main())
