"""Presentation mode: the simulated fleet, and the rules that keep it from passing as real."""

from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from shapely import Point, box, unary_union

from app.core.config import SIMULATED_LABEL, get_common_settings
from app.ingestion.settings import get_senses_settings
from app.telemetry.demo import TOWNS
from tests.tools import demo_fleet
from tests.tools.demo_fleet import Fleet, Truck, closes_roads, glide_path, legs_to_stops, worth_a_visit
from tests.tools.sim_driver import metres

OPERATOR = {"X-Operator-Token": "operator-code-long-enough-for-production-use"}
PASSWORD = "correct horse battery"
DRILL = {"type": "Polygon", "coordinates": [[[80.60, 16.49], [80.66, 16.49], [80.66, 16.53], [80.60, 16.53], [80.60, 16.49]]]}
NEW_DRIVER = {"driver_name": "Test Driver", "plate": "AP 16 TX 9999", "capacity_kg": 8000, "town": "Vijayawada"}


def test_the_simulator_knows_every_town_the_screens_offer():
    assert set(demo_fleet.TOWN_CENTRES) == set(TOWNS)


def test_there_is_a_name_for_every_truck():
    assert len(demo_fleet.DRIVERS) == len(TOWNS) * demo_fleet.TRUCKS_PER_TOWN
    assert len(set(demo_fleet.DRIVERS)) == len(demo_fleet.DRIVERS)


def test_a_route_is_cut_exactly_at_its_stops_in_order():
    line = [(80.0 + i / 100, 16.0) for i in range(11)]
    stops = [(80.03, 16.0005), (80.08, 16.0)]
    first, second = legs_to_stops(line, stops)
    assert first[-1] == stops[0] and second[-1] == stops[1]
    assert first[0] == line[0] and second[0] == line[3]


def test_a_site_visited_twice_is_not_mistaken_for_the_later_visit():
    out_and_back = [(80.0, 16.0), (80.1, 16.0), (80.0, 16.0)]
    first, second, third = legs_to_stops(out_and_back, [(80.0, 16.0), (80.1, 16.0), (80.0, 16.0)])
    assert len(first) == 2 and second[-1] == (80.1, 16.0) and third[-1] == (80.0, 16.0)


def test_a_stop_far_from_the_line_still_gets_a_leg_that_ends_there():
    [leg] = legs_to_stops([(80.0, 16.0), (80.1, 16.0)], [(80.1, 16.02)])
    assert leg[-1] == (80.1, 16.02)


def test_a_truck_resuming_on_a_road_it_passes_twice_stays_on_its_own_pass():
    """Out to 80.2, back, then out again: restarted on the first pass, it must not jump to the third."""
    east = [(80.0 + i / 100, 16.0) for i in range(21)]
    line = [*east, *reversed(east[:-1]), *east[1:]]
    stops = [(80.2, 16.0), (80.0, 16.0), (80.2, 16.0)]
    legs = demo_fleet.remaining_legs(line, stops, completed=0, position=(80.05, 16.0))
    assert len(legs) == 3
    assert legs[0][0] == (80.05, 16.0) and legs[0][-1] == stops[0]  # still heading out to the first stop
    assert all(metres(a, b) < 1200 for a, b in zip(legs[0], legs[0][1:], strict=False))  # no straight-line jump


def test_a_truck_resuming_after_a_stop_starts_on_the_next_leg():
    line = [(80.0 + i / 100, 16.0) for i in range(11)]
    stops = [(80.03, 16.0), (80.08, 16.0)]
    [leg] = demo_fleet.remaining_legs(line, stops, completed=1, position=(80.05, 16.0))
    assert leg[0] == (80.05, 16.0) and leg[-1] == stops[1]


def test_a_fast_truck_reports_a_fix_at_every_stop():
    line = [(80.0 + i / 100, 16.0) for i in range(21)]
    stops = [(80.05, 16.0), (80.15, 16.0)]
    truck = Truck(id="t", label="x", token="x", position=line[0], route_id="r", legs=legs_to_stops(line, stops))
    fixes = []
    while truck.legs or truck.pause:
        assert truck.step(40_000) is True  # 40 km a tick would otherwise jump straight past both
        fixes.append(truck.position)
    assert all(any(metres(fix, stop) < 1 for fix in fixes) for stop in stops)
    assert fixes.index(stops[0]) < fixes.index(stops[1])


def test_a_parked_truck_reports_often_enough_to_stay_online():
    assert demo_fleet.IDLE_FIX_S < 120  # v_vehicle_status calls it offline after that
    truck = Truck(id="t", label="x", token="x", position=(80.0, 16.0))
    assert truck.step(100) is True
    truck.last_fix = demo_fleet.time.monotonic()
    assert truck.step(100) is False


def test_a_truck_that_lost_gps_says_nothing_and_one_off_route_keeps_reporting_from_elsewhere():
    lost = Truck(id="t", label="x", token="x", position=(80.0, 16.0), mode="gps_lost")
    assert lost.step(100) is False
    gone = Truck(id="t", label="x", token="x", position=(80.0, 16.0), mode="off_route", route_id="r")
    assert gone.step(100) is True and metres(gone.position, (80.0, 16.0)) > 500  # the Route Monitor's threshold
    place = gone.position
    assert gone.step(100) is True and gone.position == place  # three fixes in a row are what it looks for


def test_leaving_a_route_that_doubles_back_goes_somewhere_it_never_passes():
    position = (80.70, 16.45)
    near_the_first_step = [(80.70 + i / 500, 16.45 + j * 0.02) for i in range(25) for j in range(-2, 3)]
    spot = demo_fleet.away_from(near_the_first_step, position)
    assert min(metres(spot, point) for point in near_the_first_step) > 500


def test_a_glide_ends_on_the_spot():
    assert glide_path((80.0, 16.0), (81.0, 17.0))[-1] == (81.0, 17.0)


def test_drills_come_first_and_minor_alerts_are_left_alone():
    feature = lambda drill, severity: {"properties": {"is_drill": drill, "severity": severity}}  # noqa: E731
    wanted = worth_a_visit([feature(False, "severe"), feature(False, "minor"), feature(True, "moderate")])
    assert [f["properties"]["is_drill"] for f in wanted] == [True, False]


def test_only_what_closes_roads_is_kept_clear():
    assert closes_roads({"is_drill": True, "hazard_type": "storm", "severity": "minor"})
    assert closes_roads({"is_drill": False, "hazard_type": "flood", "severity": "severe"})
    assert not closes_roads({"is_drill": False, "hazard_type": "heavy_rain", "severity": "severe"})


class ParkedFleet(Fleet):
    """Facility lookups answered from a list, for the choice of who goes where."""

    def __init__(self, trucks, spots):
        super().__init__(None, 400)
        self.trucks = {t.id: t for t in trucks}
        self.parking = spots

    async def spots(self, bounds, kinds=demo_fleet.SPOT_KINDS):
        return self.parking


ZONE = box(80.60, 16.49, 80.66, 16.53)
GROUND = [(80.70 + i / 100, 16.50) for i in range(30)]


def far_trucks(count):
    return [Truck(id=str(i), label=str(i), token="x", position=(83.0, 18.0 + i / 100)) for i in range(count)]


async def test_at_least_ten_trucks_gather_around_a_drill_all_clear_of_it():
    fleet = ParkedFleet(far_trucks(14), GROUND)
    assert await fleet.send_trucks(ZONE, ZONE, demo_fleet.DRILL_TRUCKS, "h", drill=True) >= 10
    going = [t for t in fleet.trucks.values() if t.glide]
    assert len(going) == 10 and all(not ZONE.contains(Point(t.glide[-1])) for t in going)
    assert len({t.glide[-1] for t in going}) == 10


async def test_trucks_already_near_count_towards_the_ten():
    near = [Truck(id=f"n{i}", label="n", token="x", position=(80.70, 16.5 + i / 100)) for i in range(7)]
    fleet = ParkedFleet([*near, *far_trucks(5)], GROUND)
    await fleet.send_trucks(ZONE, ZONE, 10, "h", drill=True)
    assert sum(bool(t.glide) for t in fleet.trucks.values()) == 3
    assert not any(t.glide for t in near)


async def test_a_truck_inside_a_new_drill_is_moved_out_even_if_there_are_enough():
    inside = Truck(id="in", label="in", token="x", position=(80.63, 16.51))
    near = [Truck(id=f"n{i}", label="n", token="x", position=(80.70, 16.5 + i / 100)) for i in range(10)]
    fleet = ParkedFleet([inside, *near], GROUND)
    await fleet.send_trucks(ZONE, ZONE, 10, "h", drill=True)
    assert len(inside.glide) == 1 and not ZONE.contains(Point(inside.glide[-1]))  # at once: planning starts in seconds


async def test_a_presenters_truck_gets_the_nearest_post_at_a_drill_but_ignores_real_alerts():
    me = Truck(id="me", label="me", token="x", person=True, position=(83.5, 18.5))
    fleet = ParkedFleet([*far_trucks(3), me], GROUND)
    await fleet.send_trucks(box(80.0, 16.0, 80.1, 16.1), box(80.0, 16.0, 80.1, 16.1), 2, "alert", drill=False)
    assert not me.glide
    await fleet.send_trucks(ZONE, ZONE, 2, "drill", drill=True)
    assert me.glide[-1] == sorted(GROUND, key=lambda p: ZONE.distance(Point(p)))[0]


async def test_a_real_alert_can_take_trucks_only_if_they_are_not_posted_elsewhere():
    posted = Truck(id="p", label="p", token="x", position=(83.0, 18.0), post="other")
    fleet = ParkedFleet([posted], GROUND)
    await fleet.send_trucks(ZONE, unary_union([]), 2, "alert", drill=False)
    assert not posted.glide


async def test_an_alert_with_nowhere_to_wait_sends_nobody():
    fleet = ParkedFleet(far_trucks(3), [])
    assert await fleet.send_trucks(ZONE, ZONE, 2, "sea", drill=False) is None
    assert not any(t.glide for t in fleet.trucks.values())


@pytest.fixture
async def app(live_env, monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("OPERATOR_TOKEN", OPERATOR["X-Operator-Token"])
    monkeypatch.setenv("DEMO_FLEET", "true")
    get_common_settings.cache_clear()
    get_senses_settings.cache_clear()
    yield create_app(("app.api_senses.router", "app.api_brain.router"))
    get_common_settings.cache_clear()
    get_senses_settings.cache_clear()


def browser(app, headers=None) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers)


@pytest.fixture
async def http(app):
    async with browser(app, OPERATOR) as client:
        yield client


@asynccontextmanager
async def signed_in(app, role):
    async with browser(app) as client:
        body = {"email": f"{role}-{uuid4().hex[:6]}@example.org", "password": PASSWORD, "name": "Test Person", "role": role}
        response = await client.post("/api/auth/signup", json=body)
        assert response.status_code == 201, response.text
        account = response.json()
        if role in {"operator", "driver"}:
            approved = await client.post(f"/api/accounts/{account['id']}/approve", headers=OPERATOR)
            assert approved.status_code == 200, approved.text
            signed_in = await client.post(
                "/api/auth/signin", json={"email": account["email"], "password": PASSWORD})
            assert signed_in.status_code == 200, signed_in.text
        yield client


@pytest.mark.integration
async def test_it_is_all_off_unless_asked_for(live_env, monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("DEMO_FLEET", "false")
    monkeypatch.setenv("OPERATOR_TOKEN", OPERATOR["X-Operator-Token"])
    get_common_settings.cache_clear()
    async with browser(create_app(("app.api_senses.router",)), OPERATOR) as client:
        assert (await client.get("/api/demo")).json() == {"enabled": False}
        assert (await client.get("/api/demo/fleet")).status_code == 404
        assert (await client.post("/api/demo/drivers", json=NEW_DRIVER)).status_code == 404
        assert (await client.delete("/api/demo")).status_code == 404
    get_common_settings.cache_clear()


@pytest.mark.integration
async def test_a_stranger_gets_no_tokens_and_cannot_add_or_steer_trucks(app, http):
    added = (await http.post("/api/demo/drivers", json=NEW_DRIVER)).json()
    async with browser(app) as stranger:
        assert (await stranger.get("/api/demo")).status_code == 200
        assert "device_token" not in (await stranger.get("/api/demo")).text
        assert (await stranger.get("/api/demo/fleet")).status_code == 401
        assert (await stranger.post("/api/demo/drivers", json=NEW_DRIVER)).status_code == 401
        assert (await stranger.post(f"/api/demo/drivers/{added['id']}/mode", json={"mode": "gps_lost"})).status_code == 401
        assert (await stranger.delete("/api/demo")).status_code == 401


@pytest.mark.integration
async def test_a_driver_added_by_hand_is_labelled_has_no_phone_and_reports_like_any_truck(http, live_env):
    response = await http.post("/api/demo/drivers", json=NEW_DRIVER)
    assert response.status_code == 201, response.text
    truck = response.json()
    assert truck["simulated"] is True and truck["label"] == f"{SIMULATED_LABEL} AP 16 TX 9999" and truck["on_route"] is False
    assert await live_env.fetchval("SELECT driver_phone FROM vehicles WHERE id = $1", truck["id"]) is None
    [row] = (await http.get("/api/demo/fleet")).json()
    assert row["vehicle_id"] == truck["id"] and row["route_id"] is None and row["removed"] is False
    fix = {"vehicle_id": truck["id"], "fixes": [{"lon": 80.6, "lat": 16.5, "recorded_at": "2026-11-01T00:00:00+00:00"}]}
    fix["fixes"][0]["recorded_at"] = demo_fleet.datetime.now(demo_fleet.UTC).isoformat()
    assert (await http.post("/api/telemetry", json=fix, headers={"X-Device-Token": row["device_token"]})).status_code == 202
    [listed] = (await http.get("/api/vehicles")).json()
    assert listed["simulated"] is True and listed["is_offline"] is False


@pytest.mark.integration
async def test_an_unknown_town_is_refused(http):
    assert (await http.post("/api/demo/drivers", json=NEW_DRIVER | {"town": "Atlantis"})).status_code == 422


@pytest.mark.integration
async def test_a_real_truck_is_not_marked_simulated(app, http):
    async with signed_in(app, "driver") as driver:
        body = {"label": "AP 16 TX 1", "driver_language": "te", "capacity_kg": 5000}
        assert (await driver.post("/api/driver/vehicle", json=body)).json()["vehicle"]["simulated"] is False


@pytest.mark.integration
async def test_a_driver_outside_the_state_can_switch_their_own_truck_to_simulated_gps(app, http):
    async with signed_in(app, "driver") as driver:
        registered = (await driver.post("/api/driver/vehicle", json={"label": "AP 16 TX 1234", "driver_language": "te", "capacity_kg": 10})).json()
        vehicle, phone_token = registered["vehicle"], registered["device_token"]
        response = await driver.post("/api/demo/my-truck", json={"town": "Ongole", "capacity_kg": 8000})
        assert response.status_code == 200, response.text
        assert response.json()["simulated"] is True and response.json()["label"] == f"{SIMULATED_LABEL} AP 16 TX 1234"
        assert response.json()["capacity_kg"] == 8000

        now = demo_fleet.datetime.now(demo_fleet.UTC).isoformat()
        fix = {"vehicle_id": vehicle["id"], "fixes": [{"lon": 76.9, "lat": 31.7, "recorded_at": now}]}
        assert (await driver.post("/api/telemetry", json=fix, headers={"X-Device-Token": phone_token})).status_code == 401
        assert (await driver.post("/api/driver/vehicle/device")).status_code == 409  # would cut the simulator off
        assert (await driver.post("/api/demo/my-truck", json={"town": "Ongole", "capacity_kg": 8000})).status_code == 409
        # The signed-in driver can read their route without a phone's token: 404 is "no route", not "who are you".
        assert (await driver.get(f"/api/vehicles/{vehicle['id']}/assignment")).status_code == 404
    [row] = (await http.get("/api/demo/fleet")).json()
    assert row["has_person"] is True and row["town"] == "Ongole"


@pytest.mark.integration
async def test_a_signed_in_driver_cannot_read_someone_elses_route(app, http):
    other = (await http.post("/api/demo/drivers", json=NEW_DRIVER)).json()
    async with signed_in(app, "driver") as driver:
        assert (await driver.get(f"/api/vehicles/{other['id']}/assignment")).status_code == 401


@pytest.mark.integration
async def test_a_supplier_cannot_simulate_a_truck(app):
    async with signed_in(app, "supplier") as supplier:
        assert (await supplier.post("/api/demo/my-truck", json={"town": "Ongole", "capacity_kg": 8000})).status_code == 401


@pytest.mark.integration
async def test_the_duty_officer_can_make_a_truck_misbehave(http):
    truck = (await http.post("/api/demo/drivers", json=NEW_DRIVER)).json()
    assert (await http.post(f"/api/demo/drivers/{truck['id']}/mode", json={"mode": "gps_lost"})).status_code == 200
    assert (await http.get("/api/demo/fleet")).json()[0]["mode"] == "gps_lost"
    assert (await http.post(f"/api/demo/drivers/{truck['id']}/mode", json={"mode": "flying"})).status_code == 422
    assert (await http.post(f"/api/demo/drivers/{uuid4()}/mode", json={"mode": "normal"})).status_code == 404


@pytest.mark.integration
async def test_the_fleet_comes_up_online_and_gathers_around_a_drill_without_duplicates(http, live_env):
    await live_env.execute(
        """INSERT INTO facilities (source, source_ref, kind, name, geom, fetched_at)
           SELECT 'osm', 'node/' || i, 'fuel', 'Pump ' || i, ST_SetSRID(ST_MakePoint(80.67 + i / 200.0, 16.56), 4326), now()
           FROM generate_series(1, 30) i""")
    fleet = Fleet(http, 400)
    await fleet.seed()
    await fleet.seed()  # a restarted simulator must not create a second fleet
    await fleet.tick()
    await fleet.tick()
    expected = len(TOWNS) * demo_fleet.TRUCKS_PER_TOWN
    trucks = (await http.get("/api/vehicles")).json()
    assert len(trucks) == expected and all(t["simulated"] and not t["is_offline"] and t["position"] for t in trucks)
    assert len({t["label"] for t in trucks}) == expected
    assert (await http.get("/api/demo")).json()["trucks"] == expected
    assert await live_env.fetchval("SELECT count(*) FROM vehicles WHERE driver_phone IS NOT NULL") == 0
    assert all(t["label"].split()[2] == "AP" or t["label"].startswith(SIMULATED_LABEL) for t in trucks)
    assert {row["item_code"] for row in await live_env.fetch("SELECT DISTINCT item_code::text FROM stock")} == {"WATER_L"}
    assert await live_env.fetchval("SELECT count(*) FROM suppliers WHERE name LIKE 'TEST-SIM%' AND verified") == len(TOWNS)

    drill = {"geometry": DRILL, "hazard_type": "flood", "severity": "severe", "label": "Krishna", "ttl_minutes": 30}
    assert (await http.post("/api/drill", json=drill)).status_code == 201
    for _ in range(demo_fleet.GLIDE_TICKS + 1):
        await fleet.tick()
    zone = box(80.60, 16.49, 80.66, 16.53)
    around = [t for t in fleet.trucks.values() if t.distance_to(zone) <= demo_fleet.NEAR_DEG]
    assert len(around) >= demo_fleet.DRILL_TRUCKS and not any(zone.contains(Point(t.position)) for t in around)


@pytest.mark.integration
async def test_ending_presentation_mode_leaves_a_clean_system(app, http, live_env):
    fleet = Fleet(http, 400)
    await fleet.seed()
    async with signed_in(app, "driver") as driver:
        await driver.post("/api/driver/vehicle", json={"label": "AP 16 TX 1234", "driver_language": "te", "capacity_kg": 10})
        await driver.post("/api/demo/my-truck", json={"town": "Ongole", "capacity_kg": 8000})
        assert (await http.delete("/api/demo")).status_code == 204
        assert await live_env.fetchval("SELECT count(*) FROM demo_drivers") == 0
        assert await live_env.fetchval("SELECT count(*) FROM stock") == 0
        assert await live_env.fetchval("SELECT count(*) FROM vehicles WHERE status <> 'maintenance' AND account_id IS NULL") == 0
        mine = (await driver.get("/api/driver/vehicle")).json()
        assert mine["label"] == "AP 16 TX 1234" and mine["simulated"] is False  # theirs again, and a phone can take over
        assert (await driver.post("/api/driver/vehicle/device")).status_code == 200

    await fleet.seed()  # turned on again for another presentation: the depots must have water again
    assert await live_env.fetchval("SELECT count(*) FROM stock") == len(TOWNS)
    assert (await http.get("/api/demo")).json()["trucks"] == len(TOWNS) * demo_fleet.TRUCKS_PER_TOWN
    await fleet.seed()  # with closed depots in the search results too, a restart must not stock a second set
    assert await live_env.fetchval("SELECT count(*) FROM stock") == len(TOWNS)


@pytest.mark.integration
async def test_a_duty_officer_can_list_every_supplier_and_nobody_else_can(app, http, live_env):
    facility = await live_env.fetchval(
        """INSERT INTO facilities (source, source_ref, kind, name, geom, fetched_at)
           VALUES ('osm', 'node/9', 'godown', 'Tuni godown', ST_SetSRID(ST_MakePoint(82.5, 17.3), 4326), now()) RETURNING id""")
    shop = await live_env.fetchval("INSERT INTO suppliers (facility_id, name) VALUES ($1, 'PDS Tuni') RETURNING id", facility)
    await live_env.execute(
        """INSERT INTO stock (supplier_id, item_code, quantity, unit_weight_kg, reported_at, reported_via)
           VALUES ($1, 'RICE_KG', 800, 1, now(), 'portal')""", shop)
    async with signed_in(app, "supplier") as supplier:
        await supplier.post("/api/supplier/me", json={"name": "Has An Account", "lon": 80.6, "lat": 16.5})
        assert (await supplier.get("/api/suppliers")).status_code == 401
        listed = (await http.get("/api/suppliers")).json()
    assert [s["name"] for s in listed] == ["Has An Account", "PDS Tuni"]  # accounts first
    tuni = listed[1]
    assert tuni["site"] == "Tuni godown" and tuni["has_account"] is False and tuni["verified"] is False
    assert tuni["stock"] == [{"item_code": "RICE_KG", "available": 800, "reserved": 0}]
    assert listed[0]["stock"] == [] and listed[0]["reported_at"] is None
    async with browser(app) as stranger:
        assert (await stranger.get("/api/suppliers")).status_code == 401


@pytest.mark.integration
async def test_a_supplier_sees_only_their_own_orders(app, http, live_env):
    async with signed_in(app, "supplier") as supplier, signed_in(app, "supplier") as other:
        for client, name in ((supplier, "Ours"), (other, "Theirs")):
            assert (await client.post("/api/supplier/me", json={"name": name, "lon": 80.6, "lat": 16.5})).status_code == 201
        ours = await live_env.fetchrow("SELECT id, facility_id FROM suppliers WHERE name = 'Ours'")
        plan = await live_env.fetchval(
            """INSERT INTO plans (thread_id, trigger_kind, hazard_ids, requested_by, status, is_drill)
               VALUES ('t', 'drill', '{}', 'test', 'dispatched', true) RETURNING id""")
        order = await live_env.fetchval(
            """INSERT INTO orders (plan_id, supplier_id, destination_facility_id, status, idempotency_key)
               VALUES ($1, $2, $3, 'dispatched', 'k') RETURNING id""", plan, ours["id"], ours["facility_id"])
        await live_env.execute("INSERT INTO order_lines (order_id, item_code, quantity) VALUES ($1, 'WATER_L', 500)", order)
        [row] = (await supplier.get("/api/supplier/orders")).json()
        assert row["status"] == "dispatched" and row["is_drill"] is True and row["lines"] == [{"item_code": "WATER_L", "quantity": 500}]
        assert (await other.get("/api/supplier/orders")).json() == []
    async with browser(app) as stranger:
        assert (await stranger.get("/api/supplier/orders")).status_code == 401


async def active_drill(http) -> str:
    drill = {"geometry": DRILL, "hazard_type": "flood", "severity": "severe", "label": "Krishna", "ttl_minutes": 30}
    response = await http.post("/api/drill", json=drill)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def fix_at(lon: float, lat: float) -> dict:
    return {"lon": lon, "lat": lat, "recorded_at": demo_fleet.datetime.now(demo_fleet.UTC).isoformat()}


async def online_truck(http, plate="AP 16 TX 9999", at=(80.75, 16.52)) -> dict:
    truck = (await http.post("/api/demo/drivers", json=NEW_DRIVER | {"plate": plate})).json()
    [row] = [r for r in (await http.get("/api/demo/fleet")).json() if r["vehicle_id"] == truck["id"]]
    response = await http.post("/api/telemetry", json={"vehicle_id": truck["id"], "fixes": [fix_at(*at)]},
                               headers={"X-Device-Token": row["device_token"]})  # fmt: skip
    assert response.status_code == 202, response.text
    return truck


@pytest.mark.integration
async def test_every_truck_has_its_own_number(http):
    first, second = await online_truck(http, "AP 16 TX 1"), await online_truck(http, "AP 16 TX 2")
    assert second["fleet_number"] == first["fleet_number"] + 1


@pytest.mark.integration
async def test_a_request_reaches_one_driver_who_answers_it(app, http, live_env):
    hazard = await active_drill(http)
    async with signed_in(app, "driver") as driver:
        mine = (await driver.post("/api/driver/vehicle", json={"label": "AP 16 TX 5", "driver_language": "te", "capacity_kg": 8000})).json()
        vehicle = mine["vehicle"]["id"]
        await driver.post("/api/telemetry", json={"vehicle_id": vehicle, "fixes": [fix_at(80.75, 16.52)]},
                          headers={"X-Device-Token": mine["device_token"]})  # fmt: skip
        assert (await driver.get(f"/api/vehicles/{vehicle}/request")).json() is None

        sent = await http.post("/api/driver-requests", json={"vehicle_id": vehicle, "hazard_id": hazard})
        assert sent.status_code == 201, sent.text
        assert sent.json()["status"] == "sent" and sent.json()["place"] == "DRILL: Krishna"
        again = await http.post("/api/driver-requests", json={"vehicle_id": vehicle, "hazard_id": hazard})
        assert again.status_code == 409  # one at a time

        seen = (await driver.get(f"/api/vehicles/{vehicle}/request")).json()
        assert seen["id"] == sent.json()["id"]
        assert (await driver.post(f"/api/vehicles/{vehicle}/request/answer", json={"accept": True})).json()["status"] == "accepted"
        assert (await driver.post(f"/api/vehicles/{vehicle}/request/answer", json={"accept": False})).status_code == 404  # already answered
        [listed] = (await http.get("/api/driver-requests")).json()
        assert listed["status"] == "accepted" and listed["answered_at"]

    from app.agents import data

    [row] = await data.usable_vehicles(live_env, [hazard])
    assert row["requested"] is True and row["simulated"] is False
    assert (await data.usable_vehicles(live_env))[0]["requested"] is False  # only for that place


@pytest.mark.integration
async def test_a_stranger_or_another_driver_cannot_see_or_answer_a_request(app, http):
    hazard, truck = await active_drill(http), await online_truck(http)
    await http.post("/api/driver-requests", json={"vehicle_id": truck["id"], "hazard_id": hazard})
    async with signed_in(app, "driver") as other, browser(app) as stranger:
        for client in (other, stranger):
            assert (await client.get(f"/api/vehicles/{truck['id']}/request")).status_code == 401
            assert (await client.post(f"/api/vehicles/{truck['id']}/request/answer", json={"accept": True})).status_code == 401
            assert (await client.post("/api/driver-requests", json={"vehicle_id": truck["id"], "hazard_id": hazard})).status_code == 401
            assert (await client.get(f"/api/hazards/{hazard}/distances")).status_code == 401


@pytest.mark.integration
async def test_a_request_needs_an_online_free_driver_and_a_live_place(http):
    hazard, truck = await active_drill(http), await online_truck(http)
    silent = (await http.post("/api/demo/drivers", json=NEW_DRIVER | {"plate": "AP 16 TX 7"})).json()
    assert (await http.post("/api/driver-requests", json={"vehicle_id": silent["id"], "hazard_id": hazard})).status_code == 409
    assert (await http.post("/api/driver-requests", json={"vehicle_id": str(uuid4()), "hazard_id": hazard})).status_code == 404
    assert (await http.delete(f"/api/drill/{hazard}")).status_code == 204
    assert (await http.post("/api/driver-requests", json={"vehicle_id": truck["id"], "hazard_id": hazard})).status_code == 422


@pytest.mark.integration
async def test_a_withdrawn_request_stops_waiting(http):
    hazard, truck = await active_drill(http), await online_truck(http)
    sent = (await http.post("/api/driver-requests", json={"vehicle_id": truck["id"], "hazard_id": hazard})).json()
    assert (await http.delete(f"/api/driver-requests/{sent['id']}")).status_code == 204
    assert (await http.get(f"/api/vehicles/{truck['id']}/request")).json() is None
    assert (await http.post("/api/driver-requests", json={"vehicle_id": truck["id"], "hazard_id": hazard})).status_code == 201


@pytest.mark.integration
async def test_distances_put_the_nearest_truck_first(http):
    far, near = await online_truck(http, "AP 31 TX 1", (83.2, 17.7)), await online_truck(http, "AP 16 TX 2")
    metres = (await http.get(f"/api/hazards/{await active_drill(http)}/distances")).json()
    assert list(metres) == [near["id"], far["id"]] and metres[near["id"]] < 20_000 < metres[far["id"]]


@pytest.mark.integration
async def test_a_simulated_driver_answers_and_drives_to_the_place(http, live_env):
    await live_env.execute(
        """INSERT INTO facilities (source, source_ref, kind, name, geom, fetched_at)
           VALUES ('osm', 'node/1', 'fuel', 'Pump', ST_SetSRID(ST_MakePoint(80.70, 16.56), 4326), now())""")
    truck = await online_truck(http)
    fleet = Fleet(http, 400)
    await fleet.tick()
    hazard = await active_drill(http)
    await fleet.tick()
    await http.post("/api/driver-requests", json={"vehicle_id": truck["id"], "hazard_id": hazard})
    for _ in range(demo_fleet.ANSWER_TICKS + demo_fleet.GLIDE_TICKS + 1):
        await fleet.tick()
    assert (await http.get("/api/driver-requests")).json()[0]["status"] == "accepted"
    assert fleet.trucks[truck["id"]].position == (80.70, 16.56)


@pytest.mark.integration
async def test_a_person_answers_for_themselves(app, http):
    hazard = await active_drill(http)
    async with signed_in(app, "driver") as driver:
        body = {"label": "AP 16 TX 5", "driver_language": "te", "capacity_kg": 8000}
        vehicle = (await driver.post("/api/driver/vehicle", json=body)).json()["vehicle"]
        await driver.post("/api/demo/my-truck", json={"town": "Vijayawada", "capacity_kg": 8000})
        fleet = Fleet(http, 400)
        await fleet.tick()
        await fleet.tick()
        assert (await http.post("/api/driver-requests", json={"vehicle_id": vehicle["id"], "hazard_id": hazard})).status_code == 201
        for _ in range(demo_fleet.ANSWER_TICKS + 2):
            await fleet.tick()
        assert (await http.get("/api/driver-requests")).json()[0]["status"] == "sent"  # still waiting for them


async def settle(live_env, lon=80.63, lat=16.51, name="Test Nagar"):
    await live_env.execute(
        """INSERT INTO places (source, source_ref, place_type, name, geom, population, fetched_at)
           VALUES ('osm', $1, 'town', $1, ST_SetSRID(ST_MakePoint($2, $3), 4326), 9000, now())""", name, lon, lat)  # fmt: skip


@pytest.mark.integration
async def test_an_evacuation_needs_a_live_zone_with_people_in_it(http, live_env):
    hazard = await active_drill(http)
    assert (await http.post("/api/evacuations", json={"hazard_id": hazard, "people": 40})).status_code == 422  # nobody lives there
    await settle(live_env)
    created = await http.post("/api/evacuations", json={"hazard_id": hazard, "people": 40})
    assert created.status_code == 201, created.text
    assert created.json()["settlement"] == "Test Nagar"
    [listed] = (await http.get("/api/evacuations")).json()
    assert listed["stage"] == "waiting" and listed["is_drill"] is True and listed["simulated"] is False and listed["zone"] == "DRILL: Krishna"
    assert (await http.get("/api/overview")).json()["people_waiting"] == 40
    assert (await http.post("/api/evacuations", json={"hazard_id": hazard, "people": 0})).status_code == 422
    await http.delete(f"/api/drill/{hazard}")
    assert (await http.post("/api/evacuations", json={"hazard_id": hazard, "people": 40})).status_code == 422  # it ended


@pytest.mark.integration
async def test_only_a_duty_officer_can_see_or_request_evacuations(app, http):
    hazard = await active_drill(http)
    async with signed_in(app, "driver") as driver, browser(app) as stranger:
        for client in (driver, stranger):
            assert (await client.post("/api/evacuations", json={"hazard_id": hazard, "people": 40})).status_code == 401
            assert (await client.get("/api/evacuations")).status_code == 401
            assert (await client.get("/api/routes/live")).status_code == 401


@pytest.mark.integration
async def test_the_fleet_asks_for_an_evacuation_and_then_waits(http, live_env):
    await settle(live_env)
    fleet = Fleet(http, 400)
    await fleet.tick()
    await active_drill(http)
    await fleet.tick()
    assert (await http.get("/api/evacuations")).json() == []  # not straight away
    fleet.next_evacuation = 0
    await fleet.tick()
    [asked] = (await http.get("/api/evacuations")).json()
    assert asked["simulated"] is True and demo_fleet.EVACUEES[0] <= asked["people"] <= demo_fleet.EVACUEES[1]
    low, high = demo_fleet.EVACUATION_EVERY_S
    assert low - 5 < fleet.next_evacuation - demo_fleet.time.monotonic() <= high
    await fleet.tick()
    assert len((await http.get("/api/evacuations")).json()) == 1


@pytest.mark.integration
async def test_nothing_is_evacuated_while_nothing_is_active(http):
    fleet = Fleet(http, 400)
    fleet.next_evacuation = 0
    await fleet.tick()
    assert (await http.get("/api/evacuations")).json() == []


@pytest.mark.integration
async def test_ending_presentation_mode_removes_simulated_evacuations(http, live_env):
    await settle(live_env)
    hazard = await active_drill(http)
    await http.post("/api/evacuations", json={"hazard_id": hazard, "people": 40, "simulated": True})
    await http.post("/api/evacuations", json={"hazard_id": hazard, "people": 40})
    assert (await http.delete("/api/demo")).status_code == 204
    assert [e["simulated"] for e in (await http.get("/api/evacuations")).json()] == [False]


@pytest.mark.integration
async def test_simulated_depots_get_their_water_back(http, live_env):
    fleet = Fleet(http, 400)
    await fleet.seed()
    depot = await live_env.fetchrow("SELECT id, facility_id FROM suppliers WHERE name LIKE 'TEST-SIM%' LIMIT 1")
    await live_env.execute("UPDATE stock SET reported_at = now() - interval '1 hour'")  # this PC's clock and the database's differ
    plan = await live_env.fetchval(
        """INSERT INTO plans (thread_id, trigger_kind, hazard_ids, requested_by, status, is_drill)
           VALUES ('t', 'drill', '{}', 'test', 'dispatched', true) RETURNING id""")
    order = await live_env.fetchval(
        """INSERT INTO orders (plan_id, supplier_id, destination_facility_id, status, idempotency_key, delivered_at)
           VALUES ($1, $2, $3, 'delivered', 'k', now()) RETURNING id""", plan, depot["id"], depot["facility_id"])
    await live_env.execute("INSERT INTO order_lines (order_id, item_code, quantity) VALUES ($1, 'WATER_L', 60000)", order)
    left = "SELECT available FROM v_available_stock WHERE supplier_id = $1"
    assert await live_env.fetchval(left, depot["id"]) == 0
    assert (await http.post("/api/demo/restock")).status_code == 204
    assert await live_env.fetchval(left, depot["id"]) == demo_fleet.DEPOT_WATER_L
    fleet.next_restock = 0
    await fleet.tick()
    assert fleet.next_restock > demo_fleet.time.monotonic()
