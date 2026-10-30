"""Multi-depot capacitated VRP with time windows (OR-Tools).

Inputs are node indices into a travel-time matrix that comes from Valhalla (already
hazard-aware). Each vehicle starts and ends at its own depot node. Stops that cannot be
served (capacity, time window, or no route) are dropped at a penalty and reported as
`unserved` instead of making the whole problem infeasible: the planner decides what to do
with them (split, other hub, escalate).

Times are integer seconds from the planning epoch; loads are kilograms rounded up.
"""

from __future__ import annotations

import math
from typing import Self

from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from pydantic import BaseModel, ConfigDict, Field, model_validator

# A stop is worth far more than any route's travel time, so the solver only drops a stop
# when serving it is impossible.
DROP_PENALTY = 10_000_000


class VrpVehicle(BaseModel):
    model_config = ConfigDict(frozen=True)

    vehicle_id: str
    start_node: int = Field(ge=0)
    end_node: int = Field(ge=0)
    capacity_kg: float = Field(gt=0)
    available_from_s: int = Field(default=0, ge=0)
    available_until_s: int | None = Field(default=None, ge=0)


class VrpStop(BaseModel):
    model_config = ConfigDict(frozen=True)

    stop_id: str
    node: int = Field(ge=0)
    demand_kg: float = Field(ge=0)
    window_start_s: int = Field(default=0, ge=0)
    window_end_s: int | None = Field(default=None, ge=0)
    service_s: int = Field(default=0, ge=0)


class VrpProblem(BaseModel):
    """`durations_s[i][j]` is travel time from node i to j; None means no safe route."""

    model_config = ConfigDict(frozen=True)

    durations_s: list[list[float | None]]
    vehicles: list[VrpVehicle] = Field(min_length=1)
    stops: list[VrpStop]
    horizon_s: int = Field(gt=0, description="Latest time anything may happen")
    time_limit_s: float = Field(default=5.0, gt=0)

    @model_validator(mode="after")
    def _check(self) -> Self:
        n = len(self.durations_s)
        if any(len(row) != n for row in self.durations_s):
            raise ValueError("durations_s must be square")
        depot_nodes = {v.start_node for v in self.vehicles} | {v.end_node for v in self.vehicles}
        stop_nodes = [s.node for s in self.stops]
        if any(node >= n for node in depot_nodes | set(stop_nodes)):
            raise ValueError("node index outside the matrix")
        if len(set(stop_nodes)) != len(stop_nodes) or depot_nodes & set(stop_nodes):
            raise ValueError("each stop needs its own node, distinct from depot nodes")
        if len({s.stop_id for s in self.stops}) != len(self.stops):
            raise ValueError("duplicate stop_id")
        return self


class StopVisit(BaseModel):
    stop_id: str
    node: int
    arrival_s: int
    delivered_so_far_kg: float


class VehicleRoute(BaseModel):
    vehicle_id: str
    nodes: list[int] = Field(description="Full node sequence including start and end depots")
    visits: list[StopVisit]
    start_s: int
    end_s: int
    load_kg: float


class VrpSolution(BaseModel):
    status: str
    routes: list[VehicleRoute] = Field(description="Only vehicles that serve at least one stop")
    unserved: list[str]
    objective: int | None


def solve(problem: VrpProblem) -> VrpSolution:
    # OR-Tools must visit every node it knows about, so the model only contains depot and
    # stop nodes; `used[k]` maps model node k back to the caller's matrix index.
    used = sorted({v.start_node for v in problem.vehicles} | {v.end_node for v in problem.vehicles}
                  | {s.node for s in problem.stops})  # fmt: skip
    model_of = {node: k for k, node in enumerate(used)}
    manager = pywrapcp.RoutingIndexManager(
        len(used), len(problem.vehicles),
        [model_of[v.start_node] for v in problem.vehicles], [model_of[v.end_node] for v in problem.vehicles],
    )  # fmt: skip
    routing = pywrapcp.RoutingModel(manager)
    stop_by_node = {s.node: s for s in problem.stops}
    unreachable = problem.horizon_s + 1  # any arc this long can never fit in the horizon

    def node_of(index: int) -> int:
        return used[manager.IndexToNode(index)]

    def travel(i: int, j: int) -> int:
        value = problem.durations_s[i][j]
        return unreachable if value is None else math.ceil(value)

    def transit(from_index: int, to_index: int) -> int:
        i, j = node_of(from_index), node_of(to_index)
        service = stop_by_node[i].service_s if i in stop_by_node else 0
        return travel(i, j) + service

    transit_cb = routing.RegisterTransitCallback(transit)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_cb)

    # Time: allow waiting up to the horizon; vehicles leave their depot when available.
    routing.AddDimension(transit_cb, problem.horizon_s, problem.horizon_s, False, "time")
    time_dim = routing.GetDimensionOrDie("time")
    for stop in problem.stops:
        end = problem.horizon_s if stop.window_end_s is None else min(stop.window_end_s, problem.horizon_s)
        time_dim.CumulVar(manager.NodeToIndex(model_of[stop.node])).SetRange(stop.window_start_s, end)
    for v_idx, vehicle in enumerate(problem.vehicles):
        until = problem.horizon_s if vehicle.available_until_s is None else min(vehicle.available_until_s, problem.horizon_s)
        time_dim.CumulVar(routing.Start(v_idx)).SetRange(vehicle.available_from_s, until)
        time_dim.CumulVar(routing.End(v_idx)).SetRange(vehicle.available_from_s, until)
        routing.AddVariableMinimizedByFinalizer(time_dim.CumulVar(routing.Start(v_idx)))
        routing.AddVariableMinimizedByFinalizer(time_dim.CumulVar(routing.End(v_idx)))

    # Capacity (kg, rounded up so we never overload).
    def demand(from_index: int) -> int:
        node = node_of(from_index)
        return math.ceil(stop_by_node[node].demand_kg) if node in stop_by_node else 0

    demand_cb = routing.RegisterUnaryTransitCallback(demand)
    routing.AddDimensionWithVehicleCapacity(
        demand_cb, 0, [math.floor(v.capacity_kg) for v in problem.vehicles], True, "load"
    )
    load_dim = routing.GetDimensionOrDie("load")

    for stop in problem.stops:
        routing.AddDisjunction([manager.NodeToIndex(model_of[stop.node])], DROP_PENALTY)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromMilliseconds(int(problem.time_limit_s * 1000))

    assignment = routing.SolveWithParameters(params)
    status = routing.status()
    status_name = {0: "not_solved", 1: "success", 2: "fail", 3: "fail_timeout", 4: "invalid",
                   5: "infeasible", 6: "optimal", 7: "feasible"}.get(int(status), str(status))  # fmt: skip
    if assignment is None:
        return VrpSolution(status=status_name, routes=[], unserved=[s.stop_id for s in problem.stops], objective=None)

    routes: list[VehicleRoute] = []
    served: set[str] = set()
    for v_idx, vehicle in enumerate(problem.vehicles):
        index = routing.Start(v_idx)
        nodes, visits = [], []
        while True:
            node = node_of(index)
            nodes.append(node)
            if node in stop_by_node:
                stop = stop_by_node[node]
                served.add(stop.stop_id)
                visits.append(StopVisit(
                    stop_id=stop.stop_id, node=node,
                    arrival_s=assignment.Value(time_dim.CumulVar(index)),
                    delivered_so_far_kg=assignment.Value(load_dim.CumulVar(index)) + math.ceil(stop.demand_kg),
                ))  # fmt: skip
            if routing.IsEnd(index):
                break
            index = assignment.Value(routing.NextVar(index))
        if visits:
            routes.append(VehicleRoute(
                vehicle_id=vehicle.vehicle_id, nodes=nodes, visits=visits,
                start_s=assignment.Value(time_dim.CumulVar(routing.Start(v_idx))),
                end_s=assignment.Value(time_dim.CumulVar(routing.End(v_idx))),
                load_kg=sum(stop_by_node[n].demand_kg for n in nodes if n in stop_by_node),
            ))  # fmt: skip
    unserved = [s.stop_id for s in problem.stops if s.stop_id not in served]
    return VrpSolution(status=status_name, routes=routes, unserved=unserved, objective=assignment.ObjectiveValue())
