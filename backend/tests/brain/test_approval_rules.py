"""Who decides: the rules or a person. Every rule on its own, so none can quietly stop working."""

import pytest

from app.agents.approval import Limits, reasons_for_a_person
from app.agents.reasoning import SUMMARY_CHARS, PlanBrief
from app.agents.schemas import Allocation, HazardFact, OrderDraft, OrderPart, PlannedRoute, Routing, Triage
from app.graph import fallbacks

LIMITS = Limits(max_kg=300_000, max_unserved=0.2, min_clearance_m=200)


def hazard(**changes) -> HazardFact:
    base = dict(hazard_id="h", source="drill", hazard_type="flood", severity="severe", headline="DRILL", area_desc=None,
                area_km2=10, is_drill=True, is_stale=False, expires_at=None)  # fmt: skip
    return HazardFact(**(base | changes))


def triage(**changes) -> Triage:
    base = dict(priority="high", planning_horizon_hours=24, rationale="A clear flood with roads to it.", recommend_human_review=False)
    return Triage(**(base | changes))


def order(weight=1000.0, verified=True) -> OrderDraft:
    return OrderDraft.model_construct(order_key="o", supplier_id="s", supplier_name="Depot", supplier_verified=verified, weight_kg=weight, lines=[])


def routing(label="TEST-SIM-GPS AP 16 TX 1", unserved=(), deferred=(), parts=None) -> Routing:
    route = PlannedRoute.model_construct(vehicle_label=label)
    return Routing.model_construct(routes=[route], parts=parts or [], unserved_order_keys=list(unserved), deferred_part_keys=list(deferred))


def part(key: str, kg: float) -> OrderPart:
    return OrderPart.model_construct(part_key=key, order_key="o", lines=[], weight_kg=kg)


def decide(hazards=None, tri=None, orders=None, route=None, verdicts=(), attempt=1):
    allocation = Allocation.model_construct(orders=orders or [order()], shortfalls=[])
    return reasons_for_a_person(hazards or [hazard()], tri or triage(), allocation, route or routing(), list(verdicts), attempt, LIMITS)


def test_a_clear_drill_needs_nobody():
    assert decide() == []


def test_a_shortfall_alone_does_not_hold_a_plan_back():
    allocation = Allocation.model_construct(orders=[order()], shortfalls=["no water"])
    assert reasons_for_a_person([hazard()], triage(), allocation, routing(), [], 1, LIMITS) == []


def test_goods_left_for_the_next_wave_do_not_hold_a_plan_back():
    parts = [part("a", 1000), part("b", 9000)]
    assert decide(route=routing(deferred=["b"], parts=parts)) == []


def test_a_big_job_is_not_a_question_when_only_a_truckload_leaves_now():
    """The Kakinada drill: 355 t allocated, under 300 t on the trucks. Waiting for a person moved nothing."""
    moving, for_later = part("a", 96_000), part("b", 259_000)
    assert decide(orders=[order(weight=355_000)], route=routing(deferred=["b"], parts=[moving, for_later])) == []


@pytest.mark.parametrize("kwargs,reason", [
    ({"hazards": [hazard(is_drill=False, severity="extreme")], "route": routing("AP 16 TX 1")}, "Extreme real alert"),
    ({"hazards": [hazard(is_drill=False)]}, "Real alert, but simulated trucks"),
    ({"hazards": [hazard(is_stale=True)]}, "Hazard data is out of date"),
    ({"tri": triage(recommend_human_review=True)}, "AI is unsure"),
    ({"orders": [order(verified=False)]}, "Unverified supplier"),
    ({"route": routing(parts=[part("a", 300_001)])}, "Over 300 t moving at once"),
    ({"route": routing(unserved=["a"], parts=[part("a", 3000), part("b", 7000)])}, "No truck for over 20% of the goods"),
    ({"verdicts": [{"min_clearance_m": {"v1": 150.0, "v2": 900.0}}]}, "Route under 200 m from a hazard"),
    ({"attempt": 2}, "Safety check rejected the first route"),
])
def test_each_unclear_case_goes_to_a_person_and_says_why(kwargs, reason):
    assert reason in decide(**kwargs)


def test_a_route_across_a_wide_warning_goes_to_a_person():
    allocation = Allocation.model_construct(orders=[order()], shortfalls=[])
    reasons = reasons_for_a_person([hazard()], triage(), allocation, routing(), [], 1, LIMITS, crosses_warning=True)
    assert reasons == ["Route crosses a wide warning area"]


def test_a_failed_llm_goes_to_a_person():
    tri = fallbacks.triage([hazard()], population=_population(), reason="HTTP 429")
    assert "AI assessment unavailable" in decide(tri=tri)


def _population():
    from app.agents.schemas import PopulationEstimate

    return PopulationEstimate(population_lower_bound=100, places_with_population=1, places_total=1, places_scaled=0, sources=[])


def test_a_real_alert_with_a_real_truck_can_go_by_itself():
    assert decide(hazards=[hazard(is_drill=False)], route=routing("AP 16 TX 1")) == []


def test_exactly_on_a_limit_is_allowed():
    assert decide(route=routing(parts=[part("a", 300_000)])) == []
    assert decide(verdicts=[{"min_clearance_m": {"v1": 200.0}}]) == []


def test_every_reason_is_short():
    many = decide(hazards=[hazard(is_drill=False, severity="extreme", is_stale=True)], tri=triage(recommend_human_review=True),
                  orders=[order(verified=False)], route=routing(parts=[part("a", 999_999)]), attempt=3)  # fmt: skip
    assert len(many) >= 5 and all(len(reason) <= 45 for reason in many)


def test_a_long_brief_is_cut_at_a_sentence_without_spending_a_retry():
    brief = PlanBrief(summary="Rice and water move to five relief points today. " * 20)
    assert len(brief.summary) <= SUMMARY_CHARS and brief.summary.endswith(".")
