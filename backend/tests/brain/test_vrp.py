"""VRP solver behaviour on small hand-made matrices (test-only data)."""

import pytest

from app.solvers.vrp import VrpProblem, VrpStop, VrpVehicle, solve


def matrix(n, base=600):
    """Symmetric travel times: |i-j| * base seconds."""
    return [[abs(i - j) * base for j in range(n)] for i in range(n)]


def test_capacity_forces_split_across_vehicles():
    p = VrpProblem(
        durations_s=matrix(4),
        vehicles=[VrpVehicle(vehicle_id="v1", start_node=0, end_node=0, capacity_kg=1000),
                  VrpVehicle(vehicle_id="v2", start_node=0, end_node=0, capacity_kg=1000)],
        stops=[VrpStop(stop_id="a", node=1, demand_kg=700), VrpStop(stop_id="b", node=2, demand_kg=700),
               VrpStop(stop_id="c", node=3, demand_kg=200)],
        horizon_s=24 * 3600, time_limit_s=1,
    )
    sol = solve(p)
    assert sol.unserved == []
    assert len(sol.routes) == 2
    assert all(r.load_kg <= 1000 for r in sol.routes)


def test_time_window_orders_visits():
    # b must be visited before 1000 s, a only after 3000 s, although a is closer.
    p = VrpProblem(
        durations_s=matrix(3, base=300),
        vehicles=[VrpVehicle(vehicle_id="v1", start_node=0, end_node=0, capacity_kg=5000)],
        stops=[VrpStop(stop_id="a", node=1, demand_kg=1, window_start_s=3000),
               VrpStop(stop_id="b", node=2, demand_kg=1, window_end_s=1000)],
        horizon_s=24 * 3600, time_limit_s=1,
    )
    [route] = solve(p).routes
    assert [v.stop_id for v in route.visits] == ["b", "a"]
    assert route.visits[0].arrival_s <= 1000 and route.visits[1].arrival_s >= 3000


def test_unreachable_and_oversized_stops_are_reported_not_fatal():
    d = matrix(4)
    d[0][3] = d[3][0] = d[1][3] = d[3][1] = d[2][3] = d[3][2] = None  # node 3 cut off by hazard
    p = VrpProblem(
        durations_s=d,
        vehicles=[VrpVehicle(vehicle_id="v1", start_node=0, end_node=0, capacity_kg=500)],
        stops=[VrpStop(stop_id="ok", node=1, demand_kg=100), VrpStop(stop_id="too_big", node=2, demand_kg=900),
               VrpStop(stop_id="cut_off", node=3, demand_kg=10)],
        horizon_s=24 * 3600, time_limit_s=1,
    )
    sol = solve(p)
    assert sorted(sol.unserved) == ["cut_off", "too_big"]
    assert [v.stop_id for v in sol.routes[0].visits] == ["ok"]


def test_multi_depot_uses_nearest_depot():
    # Line of nodes 0..5; depots at 0 and 5; stops at 1 and 4.
    p = VrpProblem(
        durations_s=matrix(6),
        vehicles=[VrpVehicle(vehicle_id="west", start_node=0, end_node=0, capacity_kg=1000),
                  VrpVehicle(vehicle_id="east", start_node=5, end_node=5, capacity_kg=1000)],
        stops=[VrpStop(stop_id="near_west", node=1, demand_kg=10), VrpStop(stop_id="near_east", node=4, demand_kg=10)],
        horizon_s=24 * 3600, time_limit_s=1,
    )
    by_vehicle = {r.vehicle_id: [v.stop_id for v in r.visits] for r in solve(p).routes}
    assert by_vehicle == {"west": ["near_west"], "east": ["near_east"]}


def test_vehicle_availability_window():
    p = VrpProblem(
        durations_s=matrix(2),
        vehicles=[VrpVehicle(vehicle_id="late", start_node=0, end_node=0, capacity_kg=100, available_from_s=5000)],
        stops=[VrpStop(stop_id="a", node=1, demand_kg=1, window_end_s=4000)],
        horizon_s=24 * 3600, time_limit_s=1,
    )
    assert solve(p).unserved == ["a"]


@pytest.mark.parametrize("bad", [
    {"durations_s": [[0, 1], [1]]},
    {"stops": [VrpStop(stop_id="x", node=0, demand_kg=1)]},
    {"stops": [VrpStop(stop_id="x", node=1, demand_kg=1), VrpStop(stop_id="x", node=2, demand_kg=1)]},
])
def test_invalid_problems_rejected(bad):
    base = dict(durations_s=matrix(3), vehicles=[VrpVehicle(vehicle_id="v", start_node=0, end_node=0, capacity_kg=1)],
                stops=[], horizon_s=100)
    with pytest.raises(ValueError):
        VrpProblem(**(base | bad))
