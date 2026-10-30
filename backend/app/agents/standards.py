"""Humanitarian minimum standards used to turn people into demand. Each value cites its source.

These are planning standards, not data: they multiply the population figure from v_places.
Change them only with a source.
"""

from dataclasses import dataclass

from reliefroute_contracts.enums import ItemCode


@dataclass(frozen=True)
class Standard:
    item: ItemCode
    per_person_per_day: float
    source: str


STANDARDS: tuple[Standard, ...] = (
    Standard(
        ItemCode.WATER_L, 15.0,
        "Sphere Handbook 2018, Water supply standard 2.1: at least 15 litres per person per day "
        "for drinking, cooking and personal hygiene",
    ),
    Standard(
        # 2,100 kcal/person/day (Sphere Handbook 2018, Food security standard 6.1) expressed as an
        # energy-equivalent ration of raw white rice at 365 kcal per 100 g (USDA FoodData Central,
        # "Rice, white, long-grain, regular, raw"). Real ration baskets mix cereals, pulses and oil.
        ItemCode.RICE_KG, round(2100 / 3650, 3),
        "Sphere 2018 Food security standard 6.1 (2,100 kcal/person/day) as raw-rice energy equivalent "
        "(365 kcal/100 g, USDA FoodData Central)",
    ),
)
