"""AePDS PDS stock -> suppliers/stock, using a slice of the real 2026-10-30 rice report."""

import json

import httpx
import pytest
from shapely import MultiPolygon, box

from app.ingestion import area_names
from app.ingestion.models import AdminArea
from app.ingestion.sources.aepds import AepdsSource, afso_keys, aggregate, match_offices, spelling_key
from app.ingestion.store import upsert_admin_areas, upsert_supplier_stock
from reliefroute_contracts.enums import ItemCode

from .conftest import fixture_text

ROWS = json.loads(fixture_text("aepds_rice_tuni_macherla.json"))


def test_aggregate_sums_closing_balance_per_afso():
    groups = aggregate(ROWS, ItemCode.RICE_KG)
    assert {k[1] for k in groups} <= {"Tuni", "Macherla"}
    assert sum(g["kg"] for g in groups.values()) == pytest.approx(sum(r["cb"] for r in ROWS))
    assert all(g["shops"] for g in groups.values())


def test_office_names_try_the_urban_or_rural_mandal_first():
    assert afso_keys("Tuni") == ["tuni"]
    assert afso_keys("Vijayawada (U)")[:2] == ["vijayawada urban", "vijayawada"]
    assert afso_keys("Gudivada (R)") == ["gudivada rural", "gudivada"]
    assert afso_keys("Rajahmundry(Rural)")[0] == "rajahmundry rural" and afso_keys("Tirupati (Rural)-2")[0] == "tirupati rural"


@pytest.mark.parametrize("office,district,expected", [
    ("Eluru Urban", "Eluru", "eluru urban"),
    ("Anantapur  Urban", "Ananthapur", "anantapur urban"),
    ("Vijayawada  Urban-Ii", "Ntr", "vijayawada urban"),
    ("Vijayawada Aso Office-I", "Ntr", "vijayawada urban"),
])
def test_town_offices_are_named_many_ways(office, district, expected):
    assert afso_keys(office, district)[0] == expected


def test_an_office_named_by_a_code_falls_back_to_its_district():
    assert "guntur" in afso_keys("Gnt Urban", "Guntur")
    assert "visakhapatnam urban" in afso_keys("Circle I Urban", "Visakhapatnam")


# Real pairs: the first spelling is AePDS's, the second OSM's.
@pytest.mark.parametrize("aepds,osm", [
    ("tadepalli", "tadepalle"), ("kaikaluru", "kaikalur"), ("thallapudi", "tallapudi"), ("mandapet", "mandapeta"),
    ("dwaraka tirumala", "dwarakatirumala"), ("yadamari", "yadamarri"), ("giddaluru", "giddalur"),
    ("jangareddigudem", "jangareddygudem"), ("munchingiputtu", "munchingi puttu"),
])
def test_spelling_variants_of_one_place_get_one_key(aepds, osm):
    assert spelling_key(aepds) == spelling_key(osm)


@pytest.mark.parametrize("a,b", [("bogole", "ongole"), ("akiveedu", "galiveedu"), ("alluru", "valluru")])
def test_different_places_with_similar_names_stay_apart(a, b):
    assert spelling_key(a) != spelling_key(b)


def _mandal(name, district):
    return {"name_norm": name, "district": district, "source_ref": name, "lon": 80.0, "lat": 16.0}


MANDALS = [_mandal("kavali", "nellore"), _mandal("kovvur", "east godavari"), _mandal("rajahmundry rural", "east godavari"),
           _mandal("mylavaram", "ntr"), _mandal("mylavaram", "ysr"), _mandal("gollapudi", "ntr"), _mandal("pulivendla", "ysr"),
           _mandal("proddatur", "ysr")]


def test_a_similar_name_in_another_district_is_refused():
    found = match_offices([("Nellore", "Kavali"), ("Nellore", "Kovur")], MANDALS)
    assert ("Nellore", "Kavali") in found and ("Nellore", "Kovur") not in found


def test_a_spelling_variant_in_the_right_district_is_accepted():
    found = match_offices([("Kadapa", "Proddatur"), ("Kadapa", "Pulivendula")], MANDALS)
    assert found[("Kadapa", "Proddatur")][1] == "exact name"
    assert ("Kadapa", "Pulivendula") not in found or found[("Kadapa", "Pulivendula")][1].startswith("spelling")


def test_a_name_in_two_districts_goes_to_the_one_this_office_is_in():
    found = match_offices([("Ntr", "Gollapudi"), ("Ntr", "Mylavaram"), ("Kadapa", "Proddatur"), ("Kadapa", "Mylavaram")], MANDALS)
    assert found[("Ntr", "Mylavaram")][0]["district"] == "ntr"
    assert found[("Kadapa", "Mylavaram")][0]["district"] == "ysr"


def test_with_no_way_to_tell_districts_a_shared_name_is_skipped():
    assert match_offices([("Somewhere", "Mylavaram")], MANDALS) == {}


def _area(ref, level, name, geom):
    return AdminArea(source_ref=ref, admin_level=level, name=name, name_norm=area_names.normalize(name), geom=MultiPolygon([geom]))


@pytest.mark.integration
async def test_fetch_and_store_pds_stock(live_env):
    async with live_env.acquire() as conn:
        await upsert_admin_areas(conn, [_area("r/1", 4, "Andhra Pradesh", box(78, 13, 85, 20)),
                                        _area("r/2", 6, "Tuni Mandal", box(82.4, 17.3, 82.6, 17.4)),
                                        _area("r/3", 6, "Macherla Mandal", box(79.4, 16.4, 79.5, 16.5))])

    def handler(request: httpx.Request):
        code = request.url.params["commCode"]
        return httpx.Response(200, json=ROWS if code == "2" else [])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await AepdsSource(poll_s=60).fetch(http, live_env)
    names = sorted(s.supplier_name for s in result.supplier_stock)
    assert all(n.startswith("AP Civil Supplies PDS stock") for n in names) and result.skipped == 0
    async with live_env.acquire() as conn:
        events = await upsert_supplier_stock(conn, result.supplier_stock)
        again = await upsert_supplier_stock(conn, result.supplier_stock)
        rows = await conn.fetch("""SELECT f.source::text, f.kind::text, f.attributes, s.verified, st.quantity, st.reported_via::text
                                   FROM stock st JOIN suppliers s ON s.id = st.supplier_id JOIN facilities f ON f.id = s.facility_id""")
    assert len(events) == len(result.supplier_stock) and again == []  # unchanged quantities emit nothing
    assert all(r["source"] == "datagov" and r["kind"] == "godown" and r["verified"] and r["reported_via"] == "api" for r in rows)
    assert all("shop locations not published" in r["attributes"]["location_basis"] for r in rows)
    assert sum(r["quantity"] for r in rows) == pytest.approx(sum(r["cb"] for r in ROWS))
