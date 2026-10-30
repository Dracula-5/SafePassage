"""Multi-depot capacitated VRP with time windows and pickup/delivery pairs (OR-Tools).

Inputs are indices into a travel-time matrix from Valhalla (already hazard-aware). Each
vehicle starts and ends at its own matrix node. Stops reference matrix nodes too; several
stops may share a node (one supplier filling several orders). Stop kinds:
  deliver  - goods preloaded at the vehicle's start; load counts up as they are delivered.
             `vehicle_id` ties a stop to the one vehicle already carrying its goods.
  pickup   - goods loaded here (+demand)
  dropoff  - goods unloaded here (-demand); always paired with a pickup via `pairs`
A pair is served by one vehicle, pickup first, or dropped together.

Stops that cannot be served (capacity, time window, no route) are dropped at a penalty and
reported in `unserved` instead of making the whole problem infeasible: the planner decides
what to do with them (split, other hub, escalate).

Times are integer seconds from the planning epoch; loads are kilograms rounded up.
"""

from __future__ import annotations

import math
from typing import Literal, Self

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
    kind: Literal["deliver", "pickup", "dropoff"] = "deliver"
    window_start_s: int = Field(default=0, ge=0)
    window_end_s: int | None = Field(default=None, ge=0)
    service_s: int = Field(default=0, ge=0)
    vehicle_id: str | None = None


class VrpProblem(BaseModel):
    """`durations_s[i][j]` is travel time from node i to j; None means no safe route."""

    model_config = ConfigDict(frozen=True)

    durations_s: list[list[float | None]]
    vehicles: list[VrpVehicle] = Field(min_length=1)
    stops: list[VrpStop]
    pairs: list[tuple[str, str]] = Field(default_factory=list, description="(pickup stop_id, dropoff stop_id)")
    horizon_s: int = Field(gt=0, description="Latest time anything may happen")
    time_limit_s: float = Field(default=5.0, gt=0)

    @model_validator(mode="after")
    def _check(self) -> Self:
        n = len(self.durations_s)
        if any(len(row) != n for row in self.durations_s):
            raise ValueError("durations_s must be square")
        nodes = {v.start_node for v in self.vehicles} | {v.end_node for v in self.vehicles} | {s.node for s in self.stops}
        if any(node >= n for node in nodes):
            raise ValueError("node index outside the matrix")
        by_id = {s.stop_id: s for s in self.stops}
        if len(by_id) != len(self.stops):
            raise ValueError("duplicate stop_id")
        paired = [sid for pair in self.pairs for sid in pair]
        if len(paired) != len(set(paired)) or any(sid not in by_id for sid in paired):
            raise ValueError("pairs must reference distinct existing stops")
        for pickup, dropoff in self.pairs:
            if (by_id[pickup].kind, by_id[dropoff].kind) != ("pickup", "dropoff"):
                raise ValueError(f"pair {pickup}->{dropoff} must be pickup -> dropoff")
            if by_id[pickup].demand_kg != by_id[dropoff].demand_kg:
                raise ValueError(f"pair {pickup}->{dropoff} must carry the same load")
        fleet = {v.vehicle_id for v in self.vehicles}
        if any(s.vehicle_id is not None and s.vehicle_id not in fleet for s in self.stops):
            raise ValueError("a stop is tied to a vehicle that is not in the fleet")
        unpaired = {s.stop_id for s in self.stops if s.kind != "deliver"} - set(paired)
        if unpaired:
            raise ValueError(f"pickup/dropoff stops without a pair: {sorted(unpaired)}")
        return self


class StopVisit(BaseModel):
    stop_id: str
    node: int
    kind: str
    arrival_s: int
    load_after_kg: float = Field(description="Load on board after this stop (deliver stops count up)")


class VehicleRoute(BaseModel):
    vehicle_id: str
    nodes: list[int] = Field(description="Matrix node sequence including start and end depots")
    visits: list[StopVisit]
    start_s: int
    end_s: int
    load_kg: float = Field(description="Total kg moved (sum of delivered or dropped-off loads)")
    peak_load_kg: float


class VrpSolution(BaseModel):
    status: str
    routes: list[VehicleRoute] = Field(description="Only vehicles that serve at least one stop")
    unserved: list[str]
    objective: int | None


STATUS_NAMES = {0: "not_solved", 1: "success", 2: "fail", 3: "fail_timeout", 4: "invalid", 5: "infeasible",
                6: "optimal", 7: "feasible"}  # fmt: skip


def solve(problem: VrpProblem) -> VrpSolution:
    # Model nodes: one per distinct depot matrix node, then one per stop (stops may share a
    # matrix node). OR-Tools must visit every model node it knows, so nothing else is added.
    depots = sorted({v.start_node for v in problem.vehicles} | {v.end_node for v in problem.vehicles})
    depot_model = {node: k for k, node in enumerate(depots)}
    matrix_of = depots + [s.node for s in problem.stops]
    stop_of_model = {len(depots) + i: s for i, s in enumerate(problem.stops)}
    model_of_stop = {s.stop_id: len(depots) + i for i, s in enumerate(problem.stops)}

    manager = pywrapcp.RoutingIndexManager(
        len(matrix_of), len(problem.vehicles),
        [depot_model[v.start_node] for v in problem.vehicles], [depot_model[v.end_node] for v in problem.vehicles],
    )  # fmt: skip
    routing = pywrapcp.RoutingModel(manager)
    solver = routing.solver()
    unreachable = problem.horizon_s + 1  # an arc this long can never fit in the horizon

    def travel(i: int, j: int) -> int:
        value = problem.durations_s[i][j]
        return unreachable if value is None else math.ceil(value)

    def transit(from_index: int, to_index: int) -> int:
        a, b = manager.IndexToNode(from_index), manager.IndexToNode(to_index)
        service = stop_of_model[a].service_s if a in stop_of_model else 0
        return travel(matrix_of[a], matrix_of[b]) + service

    transit_cb = routing.RegisterTransitCallback(transit)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_cb)
    routing.AddDimension(transit_cb, problem.horizon_s, problem.horizon_s, False, "time")
    time_dim = routing.GetDimensionOrDie("time")
    for model_node, stop in stop_of_model.items():
        end = problem.horizon_s if stop.window_end_s is None else min(stop.window_end_s, problem.horizon_s)
        time_dim.CumulVar(manager.NodeToIndex(model_node)).SetRange(stop.window_start_s, end)
    for v_idx, vehicle in enumerate(problem.vehicles):
        until = problem.horizon_s if vehicle.available_until_s is None else min(vehicle.available_until_s, problem.horizon_s)
        for idx in (routing.Start(v_idx), routing.End(v_idx)):
            time_dim.CumulVar(idx).SetRange(vehicle.available_from_s, until)
            routing.AddVariableMinimizedByFinalizer(time_dim.CumulVar(idx))

    def load_delta(model_node: int) -> int:
        stop = stop_of_model.get(model_node)
        if stop is None:
            return 0
        amount = math.ceil(stop.demand_kg)
        return -amount if stop.kind == "dropoff" else amount

    demand_cb = routing.RegisterUnaryTransitCallback(lambda index: load_delta(manager.IndexToNode(index)))
    routing.AddDimensionWithVehicleCapacity(demand_cb, 0, [math.floor(v.capacity_kg) for v in problem.vehicles], True, "load")
    load_dim = routing.GetDimensionOrDie("load")

    vehicle_index = {v.vehicle_id: i for i, v in enumerate(problem.vehicles)}
    for stop in problem.stops:
        index = manager.NodeToIndex(model_of_stop[stop.stop_id])
        routing.AddDisjunction([index], DROP_PENALTY)
        if stop.vehicle_id is not None:
            routing.VehicleVar(index).SetValues([-1, vehicle_index[stop.vehicle_id]])  # -1: dropped
    for pickup, dropoff in problem.pairs:
        p, d = manager.NodeToIndex(model_of_stop[pickup]), manager.NodeToIndex(model_of_stop[dropoff])
        routing.AddPickupAndDelivery(p, d)
        solver.Add(routing.ActiveVar(p) == routing.ActiveVar(d))
        solver.Add(routing.VehicleVar(p) == routing.VehicleVar(d))
        solver.Add(time_dim.CumulVar(p) <= time_dim.CumulVar(d))

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION if problem.pairs
        else routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )  # fmt: skip
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromMilliseconds(int(problem.time_limit_s * 1000))

    assignment = routing.SolveWithParameters(params)
    status_name = STATUS_NAMES.get(int(routing.status()), str(routing.status()))
    if assignment is None:
        return VrpSolution(status=status_name, routes=[], unserved=[s.stop_id for s in problem.stops], objective=None)

    routes: list[VehicleRoute] = []
    served: set[str] = set()
    for v_idx, vehicle in enumerate(problem.vehicles):
        index = routing.Start(v_idx)
        nodes, visits = [], []
        while True:
            model_node = manager.IndexToNode(index)
            nodes.append(matrix_of[model_node])
            stop = stop_of_model.get(model_node)
            if stop is not None:
                served.add(stop.stop_id)
                visits.append(StopVisit(
                    stop_id=stop.stop_id, node=stop.node, kind=stop.kind,
                    arrival_s=assignment.Value(time_dim.CumulVar(index)),
                    load_after_kg=assignment.Value(load_dim.CumulVar(index)) + load_delta(model_node),
                ))  # fmt: skip
            if routing.IsEnd(index):
                break
            index = assignment.Value(routing.NextVar(index))
        if visits:
            moved = sum(stop_of_model[model_of_stop[v.stop_id]].demand_kg for v in visits if v.kind != "pickup")
            routes.append(VehicleRoute(
                vehicle_id=vehicle.vehicle_id, nodes=nodes, visits=visits,
                start_s=assignment.Value(time_dim.CumulVar(routing.Start(v_idx))),
                end_s=assignment.Value(time_dim.CumulVar(routing.End(v_idx))),
                load_kg=moved, peak_load_kg=max(v.load_after_kg for v in visits),
            ))  # fmt: skip
    unserved = [s.stop_id for s in problem.stops if s.stop_id not in served]
    return VrpSolution(status=status_name, routes=routes, unserved=unserved, objective=assignment.ObjectiveValue())
