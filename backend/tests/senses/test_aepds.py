"""AePDS PDS stock -> suppliers/stock, using a slice of the real 2026-10-30 rice report."""

import json

import httpx
import pytest
from shapely import MultiPolygon, box

from app.ingestion import area_names
from app.ingestion.models import AdminArea
from app.ingestion.sources.aepds import AepdsSource, afso_key, aggregate
from app.ingestion.store import upsert_admin_areas, upsert_supplier_stock
from reliefroute_contracts.enums import ItemCode

from .conftest import fixture_text

ROWS = json.loads(fixture_text("aepds_rice_tuni_macherla.json"))


def test_aggregate_sums_closing_balance_per_afso():
    groups = aggregate(ROWS, ItemCode.RICE_KG)
    assert {k[1] for k in groups} <= {"Tuni", "Macherla"}
    assert sum(g["kg"] for g in groups.values()) == pytest.approx(sum(r["cb"] for r in ROWS))
    assert all(g["shops"] for g in groups.values())


def test_afso_key_normalizes_suffixes():
    assert afso_key("Gudivada (R)") == "gudivada" and afso_key("Tuni") == "tuni"


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
