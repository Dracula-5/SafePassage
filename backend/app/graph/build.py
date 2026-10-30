"""Graph wiring and the runner that starts and resumes plans on a Postgres checkpointer."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from .nodes import Deps, PlanState, make_nodes

PIPELINE = ["sentinel", "needs", "procurement", "route_planner"]


def _next_or_finish(nxt: str):
    return lambda state: "finish" if state.get("outcome") else nxt


def _after_critic(state: PlanState) -> str:
    if state.get("outcome"):
        return "finish"
    return "brief" if state["verdicts"][-1]["passed"] else "route_planner"


def build_graph(deps: Deps):
    nodes = make_nodes(deps)
    g = StateGraph(PlanState)
    for name, fn in nodes.items():
        g.add_node(name, fn)
    g.add_edge(START, "sentinel")
    g.add_conditional_edges("sentinel", _next_or_finish("needs"), ["needs", "finish"])
    g.add_conditional_edges("needs", _next_or_finish("procurement"), ["procurement", "finish"])
    g.add_conditional_edges("procurement", _next_or_finish("route_planner"), ["route_planner", "finish"])
    g.add_conditional_edges("route_planner", _next_or_finish("critic"), ["critic", "finish"])
    g.add_conditional_edges("critic", _after_critic, ["brief", "route_planner", "finish"])
    g.add_edge("brief", "approval_gate")
    g.add_conditional_edges("approval_gate", _next_or_finish("dispatcher"), ["dispatcher", "finish"])
    g.add_edge("dispatcher", "finish")
    g.add_edge("finish", END)
    return g


class PlanRunner:
    """Owns the compiled graph. One instance per worker process."""

    def __init__(self, deps: Deps, checkpointer: AsyncPostgresSaver):
        self.deps = deps
        self.graph = build_graph(deps).compile(checkpointer=checkpointer)

    @staticmethod
    def config(thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}

    async def start(self, plan_id: str, thread_id: str, hazard_ids: list[str]) -> dict:
        state: PlanState = {"plan_id": plan_id, "thread_id": thread_id, "hazard_ids": hazard_ids,
                            "started_at": datetime.now(UTC).isoformat()}  # fmt: skip
        return await self.graph.ainvoke(state, self.config(thread_id))

    async def resume(self, thread_id: str, decision: dict) -> dict | None:
        """Resume a plan paused at the approval gate. Returns None if it is not paused there."""
        snapshot = await self.graph.aget_state(self.config(thread_id))
        if "approval_gate" not in snapshot.next:
            return None
        return await self.graph.ainvoke(Command(resume=decision), self.config(thread_id))
