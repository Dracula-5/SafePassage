"""Pure logic of the wave limit, stale-plan checks and replanning. No services."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from reliefroute_contracts.enums import ItemCode

from app.agents.critic import review
from app.agents.planner import first_wave
from app.agents.schemas import (
    Allocation, Demand, OrderDraft, OrderLineDraft, OrderPart, PlannedRoute, PopulationEstimate, ReliefPoint, RouteStop, Routing,
)
from app.graph.dispatcher import english_instructions, held_back_escalations
from app.graph.nodes import _alternate_hub
from app.graph.replan import _shrink_parent_order, redraft, split_remaining
from app.graph.revalidate import carried_orders, shift_etas
from app.llm.tracing import LangfuseTracer
from app.routing.repository import blocks_roads
from app.routing.valhalla import ValhallaError
from app.solvers.deviation import distance_from_route_m, distance_m, remaining_route
from app.solvers.vrp import VrpProblem, VrpStop, VrpVehicle, solve

NOW = datetime(2026, 10, 31, 6, 0, tzinfo=UTC)


def order(key: str, weight: float, dest: str = "dest-1", supplier: str = "sup-1") -> OrderDraft:
    return OrderDraft(order_key=key, supplier_id=supplier, supplier_name=supplier, supplier_verified=True,
                      supplier_facility_id=f"fac-{supplier}", supplier_lon=80.8, supplier_lat=16.5,
                      destination_facility_id=dest, weight_kg=weight, travel_s=600,
                      lines=[OrderLineDraft(item_code=ItemCode.WATER_L, quantity=weight)])  # fmt: skip


def part(key: str, order_key: str, weight: float) -> OrderPart:
    return OrderPart(part_key=key, order_key=order_key, weight_kg=weight,
                     lines=[OrderLineDraft(item_code=ItemCode.WATER_L, quantity=weight)])  # fmt: skip


def stop(kind: str, keys: list[str], name: str = "site") -> dict:
    return {"kind": kind, "order_keys": keys, "name": name, "facility_id": "f", "lon": 80.0, "lat": 16.0, "eta": NOW.isoformat()}


def planned(order_keys: list[str], stops: list[RouteStop] | None = None, peak: float = 100) -> PlannedRoute:
    return PlannedRoute(vehicle_id="v1", vehicle_label="AP-T1", driver_language="te", order_keys=order_keys,
                        stops=stops or [], geometry=[(80.0, 16.0), (80.1, 16.1)], distance_m=15000, duration_s=1200,
                        load_kg=peak, peak_load_kg=peak, capacity_kg=6000)  # fmt: skip


@pytest.mark.parametrize("hazard_type,severity,drill,expected", [
    ("flood", "severe", False, True),
    ("cyclone", "extreme", False, True),
    ("flood", "moderate", False, False),
    ("heavy_rain", "severe", False, False),
    ("storm", "extreme", False, False),
    ("heatwave", "extreme", False, False),
    ("other", "minor", True, True),
])
def test_only_some_hazards_close_roads(hazard_type, severity, drill, expected):
    assert blocks_roads(hazard_type, severity, drill) is expected


def test_first_wave_shares_capacity_across_orders_instead_of_letting_one_fill_it():
    parts = [(f"a#{i}", order("a", 1000)) for i in range(1, 6)] + [(f"b#{i}", order("b", 1000)) for i in range(1, 3)]
    chosen, deferred = first_wave(parts, 4000)
    assert [k for k, _ in chosen] == ["a#1", "b#1", "a#2", "b#2"]
    assert deferred == ["a#3", "a#4", "a#5"]


def test_first_wave_keeps_everything_when_it_fits():
    parts = [("a", order("a", 500)), ("b", order("b", 500))]
    assert first_wave(parts, 1000) == (parts, [])


def test_carried_orders_only_cover_parts_a_vehicle_takes():
    allocation = Allocation(orders=[order("a", 3000), order("b", 100)], shortfalls=[])
    routing = Routing(routes=[planned(["a#1", "a#2"])], unserved_order_keys=[], margin_m=250, vehicles_considered=1,
                      deferred_part_keys=["a#3"],
                      parts=[part("a#1", "a", 1000), part("a#2", "a", 1000), part("a#3", "a", 1000), part("b", "b", 100)])  # fmt: skip
    carried = carried_orders(allocation, routing)
    assert [o.order_key for o in carried] == ["a"]  # b was not routed at all
    assert carried[0].weight_kg == 2000 and carried[0].lines[0].quantity == 2000


def test_goods_held_back_are_shown_per_destination_and_item_with_the_reason():
    allocation = Allocation(orders=[order("a", 3000)], shortfalls=[])
    routing = Routing(routes=[], unserved_order_keys=["a#1"], margin_m=250, vehicles_considered=1, deferred_part_keys=["a#2", "a#3"],
                      parts=[part("a#1", "a", 1000), part("a#2", "a", 1000), part("a#3", "a", 1000)])  # fmt: skip
    deferred, unserved = held_back_escalations(routing, allocation)
    assert deferred["quantity_short"] == 2000 and deferred["item_code"] == "WATER_L" and "next wave" in deferred["reason"]
    assert unserved["quantity_short"] == 1000 and "not reachable" in unserved["reason"]


def test_etas_move_with_the_approval_delay():
    route = planned(["a"], [RouteStop(facility_id="f", name="n", kind="pickup", eta=NOW, order_keys=["a"], lon=80, lat=16)])
    routing = Routing(routes=[route], unserved_order_keys=[], margin_m=250, vehicles_considered=1)
    assert shift_etas(routing, timedelta(hours=2)).routes[0].stops[0].eta == NOW + timedelta(hours=2)


def test_split_remaining_separates_goods_on_the_truck_from_goods_at_the_supplier():
    stops = [stop("pickup", ["a"]), stop("pickup", ["b"]), stop("dropoff", ["a"]), stop("dropoff", ["b"])]
    parts = [part("a", "a", 100), part("b", "b", 200)]
    waiting, loaded = split_remaining(stops, 1, parts)   # only a picked up
    assert [p.part_key for p in waiting] == ["b"] and [p.part_key for p in loaded] == ["a"]
    waiting, loaded = split_remaining(stops, 3, parts)   # a delivered, b on board
    assert waiting == [] and [p.part_key for p in loaded] == ["b"]


async def test_a_rounding_residue_does_not_leave_a_zero_kilo_order_open():
    class Conn:
        def __init__(self):
            self.sql = []

        async def fetchval(self, query, *args):
            return "order-1" if "FROM orders" in query else 4949.667  # stored rounded; the parts sum to 4949.6666...

        async def execute(self, query, *args):
            self.sql.append(query)

    conn = Conn()
    await _shrink_parent_order(conn, "plan", order("a", 4949.667), [part("a", "a", 4949 + 2 / 3)])
    assert any(q.startswith("DELETE FROM order_lines") for q in conn.sql)  # not left as 0.0003 kg


def test_goods_with_no_pickup_stop_were_loaded_before_the_route_began():
    waiting, loaded = split_remaining([stop("dropoff", ["a"])], 0, [part("a", "a", 100)])
    assert waiting == [] and [p.part_key for p in loaded] == ["a"]


def test_redraft_gets_a_new_key_and_sums_the_remaining_parts():
    draft = redraft("plan-2", order("a", 3000), [part("a#2", "a", 1000), part("a#3", "a", 500)], onboard=True)
    assert draft.order_key != "a" and draft.onboard and draft.weight_kg == 1500 and draft.lines[0].quantity == 1500
    assert draft.order_key != redraft("plan-2", order("a", 3000), [part("a#1", "a", 1)], onboard=False).order_key


def test_critic_accepts_a_dropoff_of_goods_already_on_board():
    stops = [RouteStop(facility_id="f", name="n", kind="dropoff", eta=NOW, order_keys=["a"], lon=80, lat=16)]
    plain = Routing(routes=[planned(["a"], stops)], unserved_order_keys=[], margin_m=250, vehicles_considered=1)
    assert any("before picking" in v for v in review(plain, [], 1, NOW, 3600, 100).violations)
    onboard = plain.model_copy(update={"onboard_part_keys": ["a"]})
    assert review(onboard, [], 1, NOW, 3600, 100).passed


def test_driver_deadlines_are_in_local_time():
    stops = [RouteStop(facility_id="f", name="Depot", kind="pickup", eta=NOW, order_keys=["a"], lon=80, lat=16)]
    text = english_instructions(planned(["a"], stops), {"a": [OrderLineDraft(item_code=ItemCode.WATER_L, quantity=5)]}, drill=False)
    assert "by 11:30 IST" in text and "UTC" not in text  # NOW is 06:00 UTC


def test_replacement_instructions_say_they_replace_the_old_route():
    assert "NEW ROUTE" in english_instructions(planned([]), {}, drill=False, updated=True)
    assert "NEW ROUTE" not in english_instructions(planned([]), {}, drill=False)


def test_goods_on_a_truck_are_only_unloaded_by_that_truck():
    durations = [[0, 10, 500], [10, 0, 500], [500, 500, 0]]  # node 2 is nearer "near", but the goods are on "far"
    problem = VrpProblem(
        durations_s=durations, horizon_s=3600,
        vehicles=[VrpVehicle(vehicle_id="near", start_node=0, end_node=0, capacity_kg=100),
                  VrpVehicle(vehicle_id="far", start_node=1, end_node=1, capacity_kg=100)],
        stops=[VrpStop(stop_id="D:x", node=2, demand_kg=40, kind="unload", vehicle_id="far")],
    )  # fmt: skip
    routes = solve(problem).routes
    assert [r.vehicle_id for r in routes] == ["far"] and routes[0].load_kg == 40 and routes[0].peak_load_kg == 40


def test_a_truck_gets_its_room_back_after_unloading():
    # Full truck, and a pickup that only fits once its load is off: nodes are start, destination, supplier, far site.
    durations = [[0, 60, 60, 60], [60, 0, 60, 60], [60, 60, 0, 60], [60, 60, 60, 0]]
    problem = VrpProblem(
        durations_s=durations, horizon_s=3600,
        vehicles=[VrpVehicle(vehicle_id="v", start_node=0, end_node=0, capacity_kg=100)],
        stops=[VrpStop(stop_id="D:on", node=1, demand_kg=100, kind="unload", vehicle_id="v"),
               VrpStop(stop_id="P:new", node=2, demand_kg=80, kind="pickup"),
               VrpStop(stop_id="D:new", node=3, demand_kg=80, kind="dropoff")],
        pairs=[("P:new", "D:new")],
    )  # fmt: skip
    solution = solve(problem)
    assert solution.unserved == []
    assert [v.stop_id for v in solution.routes[0].visits] == ["D:on", "P:new", "D:new"]  # the load must come off first
    assert solution.routes[0].peak_load_kg == 100 and solution.routes[0].load_kg == 180


def test_an_unload_stop_needs_a_vehicle_in_the_fleet():
    with pytest.raises(ValueError, match="fleet"):
        VrpProblem(durations_s=[[0]], horizon_s=60, stops=[VrpStop(stop_id="s", node=0, demand_kg=1, kind="unload", vehicle_id="gone")],
                   vehicles=[VrpVehicle(vehicle_id="v", start_node=0, end_node=0, capacity_kg=1)])  # fmt: skip


def test_distances_are_metres():
    route = [(80.60, 16.50), (80.70, 16.50)]
    assert distance_from_route_m(route, (80.65, 16.51)) == pytest.approx(1107, abs=15)
    assert distance_from_route_m(route, (80.65, 16.50)) < 5
    assert distance_m((80.60, 16.50), (80.60, 16.51)) == pytest.approx(1107, abs=15)


def test_remaining_route_starts_where_the_vehicle_is():
    route = [(80.60, 16.50), (80.65, 16.50), (80.70, 16.50)]
    ahead = remaining_route(route, (80.65, 16.505))
    assert ahead[0][0] == pytest.approx(80.65, abs=0.001) and ahead[-1][0] == pytest.approx(80.70, abs=0.001)
    assert len(remaining_route(route, (80.70, 16.50))) == 2  # at the end: never an empty line


class HubValhalla:
    def __init__(self, durations):
        self.durations = durations

    async def matrix(self, origins, targets, exclude=None):
        if self.durations is None:
            raise ValhallaError("router down")
        return type("M", (), {"durations_s": self.durations})()


class Vehicle:
    lon, lat = 80.7, 16.5


def _hub_deps(monkeypatch, durations, candidates):
    from types import SimpleNamespace

    from app.agents import data

    async def wider(*args):
        return candidates

    monkeypatch.setattr(data, "relief_points", wider)
    return SimpleNamespace(valhalla=HubValhalla(durations), pool=None,
                           settings=SimpleNamespace(relief_search_radius_m=15000, relief_min_distance_m=1500))  # fmt: skip


def _demand(points):
    return Demand(population=PopulationEstimate(population_lower_bound=1, places_with_population=1, places_total=1, sources=[]),
                  horizon_days=1, relief_points=points, lines=[])  # fmt: skip


def _point(fid):
    return ReliefPoint(facility_id=fid, name=f"School {fid}", kind="school", lon=80.5, lat=16.5, distance_to_hazard_m=1000, share=0)


async def test_alternate_hub_needs_a_vehicle_to_reach_the_supplier_and_the_supplier_to_reach_it(monkeypatch):
    allocation = Allocation(orders=[order("a", 10)], shortfalls=[])
    points = [_point("near"), _point("far")]
    # Rows: the vehicle, then the supplier. Columns: the supplier, then each candidate.
    goods_can_reach_far = [[60, None, None], [None, None, 900]]
    deps = _hub_deps(monkeypatch, goods_can_reach_far, points)
    hub = await _alternate_hub(deps, {"hazard_ids": []}, _demand([]), [Vehicle], allocation, [])
    assert hub["facility_id"] == "far" and hub["kind"] == "alternate_hub" and "School far" in hub["reason"]


async def test_no_hub_is_proposed_when_the_supplier_itself_is_cut_off(monkeypatch):
    allocation = Allocation(orders=[order("a", 10)], shortfalls=[])
    # The vehicle reaches the hub, but nothing reaches the supplier, so moving the hub would not help.
    deps = _hub_deps(monkeypatch, [[None, 900, 900], [None, 900, 900]], [_point("near"), _point("far")])
    assert await _alternate_hub(deps, {"hazard_ids": []}, _demand([]), [Vehicle], allocation, []) is None


async def test_goods_on_a_truck_only_need_the_truck_to_reach_the_hub(monkeypatch):
    allocation = Allocation(orders=[order("a", 10).model_copy(update={"onboard": True})], shortfalls=[])
    deps = _hub_deps(monkeypatch, [[None, 900]], [_point("near"), _point("far")])
    hub = await _alternate_hub(deps, {"hazard_ids": []}, _demand([]), [Vehicle], allocation, [])
    assert hub["facility_id"] == "far"


async def test_alternate_hub_skips_points_already_tried_and_survives_a_router_outage(monkeypatch):
    allocation = Allocation(orders=[order("a", 10)], shortfalls=[])
    deps = _hub_deps(monkeypatch, [[60, 100], [None, 100]], [_point("near")])
    assert await _alternate_hub(deps, {"hazard_ids": []}, _demand([_point("near")]), [Vehicle], allocation, []) is None
    deps = _hub_deps(monkeypatch, None, [_point("far")])
    assert await _alternate_hub(deps, {"hazard_ids": []}, _demand([]), [Vehicle], allocation, []) is None


async def test_langfuse_receives_a_trace_and_generation():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers["authorization"], json.loads(request.content)
        return httpx.Response(207, json={"successes": [], "errors": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        tracer = LangfuseTracer("pk", "sk", "https://lf.example/", http)
        await tracer.record(name="Triage", model="gemini-x", system="sys", prompt="p", output="o", latency_ms=120,
                            attempts=2, error=None)  # fmt: skip
    assert seen["url"] == "https://lf.example/api/public/ingestion" and seen["auth"].startswith("Basic ")
    trace, generation = seen["body"]["batch"]
    assert trace["type"] == "trace-create" and generation["type"] == "generation-create"
    assert generation["body"]["traceId"] == trace["body"]["id"] and generation["body"]["model"] == "gemini-x"


async def test_langfuse_outage_never_reaches_the_plan():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await LangfuseTracer("pk", "sk", "https://lf.example", http).record(
            name="Triage", model="m", system="s", prompt="p", output=None, latency_ms=1, attempts=1, error="x")
