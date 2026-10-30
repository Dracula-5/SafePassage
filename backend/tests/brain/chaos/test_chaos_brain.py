"""What happens when dependencies die or stall in the middle of real work.

Runs on real PostGIS and Valhalla. Each test breaks one thing the way it breaks in production: a
closed port, a server that accepts and never answers, an LLM that only returns garbage, Redis
refusing connections, a worker dying between nodes. The rule under test is always the same: the
worker survives, nothing is invented, and a human can see what happened.
"""

import asyncio
import json
from uuid import UUID

import httpx
import pytest
from reliefroute_contracts.events import EventEnvelope, PlanRequested, envelope_adapter
from reliefroute_contracts.streams import GROUP_BRAIN, PLAN_APPROVED, PLAN_REQUESTED

from app.core import bus
from app.core.resilience import CircuitBreaker, SourceUnavailable
from app.graph import plans
from app.graph.monitor import RouteMonitor
from app.graph.worker import Handler
from app.llm.client import LLMClient
from app.llm.factory import build_llm
from app.routing.valhalla import ValhallaClient

from ..fakes import SchemaAwareBackend
from ..test_integration_closed_loop import active_route, published, replan_from, run_to_dispatch  # noqa: F401 (fixture)
from ..test_integration_graph import events, world  # noqa: F401 (fixture)

pytestmark = pytest.mark.integration
DEAD_URL = "http://127.0.0.1:1"


async def start(world):  # noqa: F811
    pool, plan = world["pool"], world["plan"]
    thread = await pool.fetchval("SELECT thread_id FROM plans WHERE id = $1", plan)
    await world["runner"].start(plan, thread, [world["hazard"]])
    return thread, await pool.fetchrow("SELECT status::text, unreachable_reason FROM plans WHERE id = $1", plan)


def dead_valhalla(http: httpx.AsyncClient) -> ValhallaClient:
    return ValhallaClient(DEAD_URL, http, retries=0, timeout_s=1)


async def test_valhalla_down_stops_the_plan_without_guessing_a_route(world):  # noqa: F811
    async with httpx.AsyncClient() as http:
        world["deps"].valhalla = dead_valhalla(http)
        _, row = await start(world)
    assert row["status"] == "failed" and "no supplier has reachable stock" in row["unreachable_reason"]
    timeline = await events(world["pool"], world["plan"])
    assert any("those suppliers count as unreachable" in e["message"] for e in timeline)
    assert await world["pool"].fetchval("SELECT count(*) FROM routes") == 0


async def test_valhalla_dying_between_procurement_and_routing_is_reported_plainly(world):  # noqa: F811
    real = world["deps"].valhalla
    original_matrix = real.matrix
    calls = 0

    async def dies_after_procurement(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise SourceUnavailable("valhalla", "connection refused")
        return await original_matrix(*args, **kwargs)

    real.matrix = dies_after_procurement
    _, row = await start(world)
    assert row["status"] == "failed" and "no route can be verified as safe" in row["unreachable_reason"]


async def test_valhalla_that_hangs_times_out_and_opens_the_breaker(world):  # noqa: F811
    async def never_answer(reader, writer):
        await asyncio.sleep(30)
        writer.close()

    server = await asyncio.start_server(never_answer, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    breaker = CircuitBreaker("valhalla", failure_threshold=1, reset_after_s=60)
    try:
        async with httpx.AsyncClient() as http:
            client = ValhallaClient(f"http://127.0.0.1:{port}", http, breaker=breaker, retries=0, timeout_s=0.3)
            world["deps"].valhalla = client
            _, row = await asyncio.wait_for(start(world), timeout=20)  # the plan cannot hang with the router
    finally:
        server.close()
    assert row["status"] == "failed" and breaker.state.value == "open"


class OutageBackend:
    model = "gemini-test"

    async def complete(self, system, turns):
        raise SourceUnavailable("gemini", "HTTP 503")


class GarbageBackend:
    model = "gemini-test"

    async def complete(self, system, turns):
        return "the cat sat on the mat"


@pytest.mark.parametrize("backend", [OutageBackend(), GarbageBackend()], ids=["outage", "garbage"])
async def test_llm_failure_uses_labelled_rules_and_still_needs_a_human(world, backend):  # noqa: F811
    world["deps"].llm = LLMClient(backend)
    _, row = await start(world)
    assert row["status"] == "awaiting_approval"
    plan = await world["pool"].fetchrow("SELECT summary, triage FROM plans WHERE id = $1", world["plan"])
    assert plan["summary"].startswith("RULE-BASED SUMMARY") and plan["triage"]["rationale"].startswith("RULE-BASED FALLBACK")
    assert plan["triage"]["recommend_human_review"] is True


async def test_no_llm_key_at_all_still_plans_and_says_so(world):  # noqa: F811
    world["deps"].llm = None
    _, row = await start(world)
    assert row["status"] == "awaiting_approval"
    assert any("LLM not configured" in e["message"] for e in await events(world["pool"], world["plan"]))


async def test_gemini_down_at_worker_start_does_not_stop_the_worker():
    from app.core.settings_brain import BrainSettings

    settings = BrainSettings(database_url="postgresql://x", redis_url="redis://x", valhalla_url=DEAD_URL, gemini_api_key="k")

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as http:
        assert await build_llm(settings, http) is None


async def test_translation_failure_sends_english_rather_than_nothing(world, published):  # noqa: F811
    backend = SchemaAwareBackend()
    real_complete = backend.complete

    async def no_translation(system, turns):
        if "Translate driver instructions" in system:
            raise SourceUnavailable("gemini", "HTTP 503")
        return await real_complete(system, turns)

    backend.complete = no_translation
    world["deps"].llm = LLMClient(backend)
    await run_to_dispatch(world)
    instructions = await world["pool"].fetchval("SELECT instructions FROM routes")
    assert instructions["language"] == "en" and "Vehicle AP-T1" in instructions["text"]


class WorkerKilled(Exception):
    pass


class CrashOnce:
    """Valhalla stand-in that dies once in Procurement, like a worker killed between nodes."""

    def __init__(self, real):
        self.real = real
        self.crash = True

    def __getattr__(self, name):
        return getattr(self.real, name)

    async def matrix(self, *args, **kwargs):
        if self.crash:
            self.crash = False
            raise WorkerKilled  # no node catches this, so the run stops here like a dead process
        return await self.real.matrix(*args, **kwargs)


def envelope_for(plan_id: str, hazard: str) -> EventEnvelope:
    return bus.build_envelope(PLAN_REQUESTED, PlanRequested(plan_id=UUID(plan_id), trigger_kind="drill", trigger_ref=None,
                                                            hazard_ids=[UUID(hazard)], requested_by="test"))  # fmt: skip


async def test_worker_killed_mid_plan_continues_from_its_checkpoint(world):  # noqa: F811
    runner, pool, plan = world["runner"], world["pool"], world["plan"]
    world["deps"].valhalla = CrashOnce(world["deps"].valhalla)
    await pool.execute("UPDATE plans SET status = 'planning' WHERE id = $1", plan)  # as the first delivery does
    thread = await pool.fetchval("SELECT thread_id FROM plans WHERE id = $1", plan)
    with pytest.raises(WorkerKilled):
        await runner.start(plan, thread, [world["hazard"]])
    assert await pool.fetchval("SELECT status::text FROM plans WHERE id = $1", plan) == "planning"
    llm_calls = len(world["backend"].calls)
    await pool.execute("UPDATE plans SET updated_at = now() - interval '5 minutes' WHERE id = $1", plan)

    await Handler(runner, [])(envelope_for(plan, world["hazard"]))  # the bus redelivers the unacked event

    assert await pool.fetchval("SELECT status::text FROM plans WHERE id = $1", plan) == "awaiting_approval"
    sentinels = [e for e in await events(pool, plan) if e["node"] == "sentinel" and e["event_type"] == "node_started"]
    assert len(sentinels) == 1  # finished nodes did not run again
    assert "sentinel" not in world["backend"].calls[llm_calls:]


async def test_a_duplicate_event_for_a_healthy_plan_does_nothing(world):  # noqa: F811
    pool, plan = world["pool"], world["plan"]
    await pool.execute("UPDATE plans SET status = 'awaiting_approval' WHERE id = $1", plan)
    await Handler(world["runner"], [])(envelope_for(plan, world["hazard"]))
    assert await pool.fetchval("SELECT count(*) FROM agent_events") == 0


async def test_worker_killed_during_dispatch_finishes_it_without_duplicates(world, published):  # noqa: F811
    runner, pool, plan = world["runner"], world["pool"], world["plan"]
    thread = await pool.fetchval("SELECT thread_id FROM plans WHERE id = $1", plan)
    await runner.start(plan, thread, [world["hazard"]])
    await pool.execute("INSERT INTO approvals (plan_id, decision, decided_by) VALUES ($1, 'approved', 'officer')", plan)

    from app.graph import dispatcher

    original = dispatcher.purchase_order_pdf

    def dies(*args, **kwargs):
        raise WorkerKilled

    dispatcher.purchase_order_pdf = dies
    with pytest.raises(WorkerKilled):
        await runner.resume(thread, {"decision": "approved", "decided_by": "officer", "reason": None})
    dispatcher.purchase_order_pdf = original
    orders = await pool.fetchval("SELECT count(*) FROM orders")
    assert orders >= 1

    await runner.resume(thread, {"decision": "approved", "decided_by": "officer", "reason": None})  # redelivered event

    assert await pool.fetchval("SELECT status::text FROM plans WHERE id = $1", plan) == "dispatched"
    assert await pool.fetchval("SELECT count(*) FROM orders") == orders
    assert list(world["docs"].glob("*/PO-*.pdf"))


async def test_redis_down_at_approval_keeps_the_decision_and_the_sweep_recovers_it(world, monkeypatch, published):  # noqa: F811
    pool, plan = world["pool"], world["plan"]
    runner = world["runner"]
    thread = await pool.fetchval("SELECT thread_id FROM plans WHERE id = $1", plan)
    await runner.start(plan, thread, [world["hazard"]])
    await pool.execute("INSERT INTO approvals (plan_id, decision, decided_by) VALUES ($1, 'approved', 'officer')", plan)
    await pool.execute("UPDATE plans SET status = 'approved', updated_at = now() - interval '5 minutes' WHERE id = $1", plan)

    assert await plans.requeue_stuck_plans(pool, 60) == 1  # what the worker's loop does once Redis is back
    assert [t for t, _ in published] == [PLAN_APPROVED]
    payload = published[0][1]
    await Handler(runner, [])(bus.build_envelope(PLAN_APPROVED, payload))
    assert await pool.fetchval("SELECT status::text FROM plans WHERE id = $1", plan) == "dispatched"


async def test_try_publish_reports_a_redis_outage_instead_of_raising(monkeypatch):
    import redis.asyncio as redis

    class Dead:
        async def xadd(self, *args, **kwargs):
            raise redis.ConnectionError("refused")

    monkeypatch.setattr(bus, "get_redis", lambda: Dead())
    payload = PlanRequested(plan_id=UUID(int=1), trigger_kind="manual", trigger_ref=None, hazard_ids=[], requested_by="t")
    assert await bus.try_publish(PLAN_REQUESTED, payload) is False


async def test_a_lost_deviation_event_is_retried_by_the_sweep(world, published, monkeypatch):  # noqa: F811
    pool = world["pool"]
    await run_to_dispatch(world)
    route = await active_route(pool)
    monitor = RouteMonitor(world["deps"])
    await pool.execute(
        """INSERT INTO route_deviations (route_id, plan_id, vehicle_id, kind, detected_at)
           VALUES ($1, $2, $3, 'off_route', now() - interval '10 minutes')""",
        route["id"], world["plan"], route["vehicle_id"])
    await monitor.sweep()
    assert await pool.fetchval("SELECT count(*) FROM plans WHERE parent_plan_id IS NOT NULL") == 1
    assert await pool.fetchval("SELECT replan_id FROM route_deviations") is not None
    await monitor.sweep()  # idempotent
    assert await pool.fetchval("SELECT count(*) FROM plans WHERE parent_plan_id IS NOT NULL") == 1


async def test_replan_that_finds_no_other_vehicle_tells_a_human(world, published):  # noqa: F811
    pool = world["pool"]
    await run_to_dispatch(world)
    route = await active_route(pool)
    await pool.execute("UPDATE routes SET status = 'active' WHERE id = $1", route["id"])
    await pool.execute("UPDATE vehicles SET last_seen_at = now() - interval '1 hour'")
    await RouteMonitor(world["deps"]).sweep()
    child = await replan_from(world, published)
    row = await pool.fetchrow("SELECT status::text, unreachable_reason, escalations FROM plans WHERE id = $1", child)
    assert row["status"] == "failed" and "no online vehicle" in row["unreachable_reason"]
    assert row["escalations"] and all("no longer on a route" in e["reason"] for e in row["escalations"])


def test_chaos_module_uses_the_real_consumer_group_name():
    assert GROUP_BRAIN == "brain" and json.loads(envelope_adapter.dump_json(bus.build_envelope(
        PLAN_REQUESTED, PlanRequested(plan_id=UUID(int=1), trigger_kind="manual", trigger_ref=None, hazard_ids=[],
                                      requested_by="t"))))["event_type"] == PLAN_REQUESTED  # fmt: skip
