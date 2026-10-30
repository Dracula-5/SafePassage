import json
from datetime import UTC, datetime, timedelta

import pytest
from shapely import LineString, box

from app.agents.critic import review
from app.agents.needs import compute_demand
from app.agents.procurement import StockRow, allocate
from app.agents.reasoning import numbers_in, translate_instructions
from app.agents.schemas import PlannedRoute, PopulationEstimate, ReliefPoint, RouteStop, Routing, Triage
from app.agents.standards import STANDARDS
from app.graph import fallbacks
from app.llm.client import LLMClient, LLMOutputError, extract_json
from app.llm.factory import pick_gemini_model
from app.solvers.geometry import HazardShape

from .fakes import ScriptedBackend


def points(n):
    return [ReliefPoint(facility_id=f"f{i}", name=f"School {i}", kind="school", lon=80 + i / 100, lat=16,
                        distance_to_hazard_m=100 * i, share=0) for i in range(n)]


def test_demand_uses_cited_standards_and_rounds_up():
    pop = PopulationEstimate(population_lower_bound=1000, places_with_population=3, places_total=10, sources=["census2011"])
    d = compute_demand(pop, points(2), horizon_days=1.5)
    water = [l for l in d.lines if l.item_code == "WATER_L"]
    assert [l.quantity for l in water] == [11250, 11250]  # 500 people x 15 L x 1.5 d each
    rice = [l for l in d.lines if l.item_code == "RICE_KG"]
    assert rice[0].quantity == 432  # ceil(500 x 0.575 x 1.5)
    assert all("Sphere" in l.basis for l in d.lines) and all(s.source for s in STANDARDS)
    assert pop.coverage == 0.3


def test_no_population_means_no_demand_not_a_guess():
    pop = PopulationEstimate(population_lower_bound=0, places_with_population=0, places_total=7, sources=[])
    assert compute_demand(pop, points(3), 1).lines == []


def stock(sid, item, available, verified=True, w=1.0):
    return StockRow(supplier_id=sid, supplier_name=sid.upper(), verified=verified, facility_id=f"fac-{sid}", lon=80,
                    lat=16, item_code=item, available=available, unit_weight_kg=w)


def test_allocation_splits_nearest_first_and_reports_shortfall():
    pop = PopulationEstimate(population_lower_bound=1000, places_with_population=1, places_total=1, sources=[])
    demand = compute_demand(pop, points(1), 1).lines  # 15000 L water, 575 kg rice
    rows = [stock("near", "WATER_L", 9000), stock("far", "WATER_L", 4000), stock("near", "RICE_KG", 1000, w=1.0)]
    travel = {("near", "f0"): 600, ("far", "f0"): 3600}
    a = allocate("plan-1", demand, rows, travel)
    by = {o.supplier_id: {l.item_code.value: l.quantity for l in o.lines} for o in a.orders}
    assert by == {"near": {"WATER_L": 9000, "RICE_KG": 575}, "far": {"WATER_L": 4000}}
    [short] = a.shortfalls
    assert (short.item_code, short.quantity_short, short.reason) == ("WATER_L", 2000, "insufficient stock across reachable suppliers")
    assert {o.order_key for o in allocate("plan-1", demand, rows, travel).orders} == {o.order_key for o in a.orders}


def test_unreachable_supplier_is_not_used():
    pop = PopulationEstimate(population_lower_bound=100, places_with_population=1, places_total=1, sources=[])
    demand = [l for l in compute_demand(pop, points(1), 1).lines if l.item_code == "WATER_L"]
    a = allocate("p", demand, [stock("cut", "WATER_L", 10_000)], {("cut", "f0"): None})
    assert a.orders == [] and a.shortfalls[0].reason == "suppliers with stock cannot reach this destination safely"
    b = allocate("p", demand, [], {})
    assert b.shortfalls[0].reason == "no supplier holds this item"


def _route(geometry, peak=900, cap=1000, eta_h=2, order=("k1", "k1")):
    start = datetime(2026, 10, 30, tzinfo=UTC)
    stops = [RouteStop(facility_id="s", name="Supplier", kind="pickup", eta=start + timedelta(hours=1), order_keys=[order[0]], lon=0, lat=0),
             RouteStop(facility_id="d", name="Camp", kind="dropoff", eta=start + timedelta(hours=eta_h), order_keys=[order[1]], lon=0, lat=0)]
    return start, PlannedRoute(vehicle_id="v1", vehicle_label="T1", driver_language="te", order_keys=["k1"], stops=stops,
                               geometry=geometry, distance_m=1, duration_s=1, load_kg=900, peak_load_kg=peak, capacity_kg=cap)


def test_critic_passes_clean_route_and_rejects_each_violation():
    hazard = HazardShape("h1", box(80.0, 16.0, 80.1, 16.1))
    start, clean = _route([(79.9, 15.9), (79.95, 15.95)])
    ok = review(Routing(routes=[clean], unserved_order_keys=[], margin_m=250, vehicles_considered=1), [hazard], 1, start, 6 * 3600, 100)
    assert ok.passed and ok.min_clearance_m["v1"] > 100
    _, bad = _route([(79.95, 16.05), (80.15, 16.05)], peak=1200, eta_h=9, order=("k2", "k1"))
    v = review(Routing(routes=[bad], unserved_order_keys=[], margin_m=250, vehicles_considered=1), [hazard], 2, start, 6 * 3600, 100)
    text = " | ".join(v.violations)
    assert not v.passed and "enters hazard h1" in text and "exceeds capacity" in text
    assert "after the planning horizon" in text and "before picking them up" in text


async def test_llm_repairs_invalid_output_then_validates():
    backend = ScriptedBackend(["not json at all", '```json\n{"priority": "high", "planning_horizon_hours": 500, '
                               '"rationale": "x"}\n```', json.dumps({"priority": "high", "planning_horizon_hours": 24,
                               "rationale": "Flooding near villages requires one day of supplies.", "key_risks": [],
                               "recommend_human_review": True})])
    result = await LLMClient(backend).generate("system", "facts", Triage)
    assert result.value.planning_horizon_hours == 24 and result.repaired and len(result.attempts) == 3
    assert "That reply was invalid" in backend.calls[-1][1][-1][1]  # the validation error was sent back
    assert "less than or equal to 72" in backend.calls[-1][1][-1][1]


async def test_llm_gives_up_after_max_attempts():
    with pytest.raises(LLMOutputError) as exc:
        await LLMClient(ScriptedBackend(["{}", "{}", "{}"])).generate("s", "p", Triage)
    assert len(exc.value.attempts) == 3


async def test_translation_must_keep_numbers():
    english = "1. Pick up at Depot by 14:30 UTC: 5000 WATER_L."
    backend = ScriptedBackend(['{"text": "డిపో వద్ద 14:30 కి 500 WATER_L"}', '{"text": "1. డిపో వద్ద 14:30 UTC కి 5000 WATER_L."}'])
    result = await translate_instructions(LLMClient(backend), english, "te")
    assert numbers_in(result.value.text) == numbers_in(english) and result.repaired


def test_extract_json_and_model_pick():
    assert extract_json('Sure! ```json {"a": 1} ``` thanks') == '{"a": 1}'
    names = ["gemini-1.5-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro", "gemini-2.0-flash-exp",
             "gemini-2.5-flash-image"]
    assert pick_gemini_model(names) == "gemini-2.5-flash"
    assert pick_gemini_model(["gemini-2.5-pro"]) is None


def test_fallbacks_are_labelled():
    pop = PopulationEstimate(population_lower_bound=10, places_with_population=1, places_total=5, sources=[])
    from app.agents.schemas import HazardFact
    h = HazardFact(hazard_id="h", source="drill", hazard_type="flood", severity="severe", headline="x", area_desc=None,
                   area_km2=1, is_drill=True, is_stale=False, expires_at=None)
    t = fallbacks.triage([h], pop, "LLM not configured")
    assert t.priority == "high" and t.rationale.startswith("RULE-BASED FALLBACK") and t.recommend_human_review
