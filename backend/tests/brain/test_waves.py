"""What the next wave is asked to carry. The waves themselves run on a real database in test_integration_graph.py."""

from app.agents.needs import still_needed
from app.agents.schemas import Demand, DemandLine, ItemCode, PopulationEstimate


def demand(*lines: tuple[str, ItemCode, float]) -> Demand:
    population = PopulationEstimate(population_lower_bound=100, places_with_population=1, places_total=1, places_scaled=0, sources=[])
    return Demand(population=population, horizon_days=1, relief_points=[], lines=[
        DemandLine(destination_facility_id=site, item_code=item, quantity=quantity, basis="test") for site, item, quantity in lines])


def left(result: Demand) -> dict:
    return {(line.destination_facility_id, line.item_code.value): line.quantity for line in result.lines}


def test_goods_already_ordered_are_not_asked_for_again():
    asked = demand(("a", ItemCode.WATER_L, 1000), ("a", ItemCode.RICE_KG, 50), ("b", ItemCode.WATER_L, 1000))
    assert left(still_needed(asked, {("a", "WATER_L"): 400, ("b", "WATER_L"): 1000})) == {("a", "WATER_L"): 600, ("a", "RICE_KG"): 50}


def test_a_line_already_covered_is_gone_even_when_more_was_ordered():
    assert left(still_needed(demand(("a", ItemCode.WATER_L, 100)), {("a", "WATER_L"): 150})) == {}


def test_a_first_plan_keeps_everything():
    asked = demand(("a", ItemCode.WATER_L, 1000))
    assert left(still_needed(asked, {})) == {("a", "WATER_L"): 1000}


def test_the_basis_stays_so_the_figure_can_still_be_traced():
    assert still_needed(demand(("a", ItemCode.WATER_L, 1000)), {("a", "WATER_L"): 1}).lines[0].basis == "test"
