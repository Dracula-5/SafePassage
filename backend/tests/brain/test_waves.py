"""What the next wave is asked to carry. The waves themselves run on a real database in test_integration_graph.py."""

from app.agents.needs import still_needed
from app.agents.schemas import Demand, DemandLine, ItemCode, OrderDraft, OrderLineDraft, PopulationEstimate
from app.graph.revalidate import stock_problems


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


class Stock:
    def __init__(self, available: float):
        self.available = available

    async def fetch(self, *_args):
        return [{"supplier_id": "s", "item_code": "WATER_L", "available": self.available}]


def order_for(quantity: float) -> OrderDraft:
    return OrderDraft.model_construct(supplier_id="s", supplier_name="Depot", lines=[OrderLineDraft(item_code=ItemCode.WATER_L, quantity=quantity)])


async def test_an_order_for_all_the_stock_is_not_stale_because_it_was_rounded_up():
    """31.0526 L rounded to 31.053 read as out of stock, and each fresh plan went stale the same way, for ever."""
    assert await stock_problems(Stock(31.052611), [order_for(31.053)]) == []


async def test_stock_that_really_ran_out_is_still_stale():
    assert await stock_problems(Stock(30.9), [order_for(31.053)]) == ["Depot now has 31 WATER_L, plan needs 31"]
