"""Andhra Pradesh ePoS (AePDS) public stock report: live closing balances of PDS commodities at
every fair-price shop (FPS), by district and mandal supply office (AFSO).

Endpoint: GET https://aepos.ap.gov.in/EposLogin/api/stock/details?month&year&commCode (public,
used by the portal's own report page; no captcha or login). Responses are large (~50 MB for
rice, minutes to serve), so commodities are fetched one at a time with long timeouts, every
few hours.

Mapping: one supplier per AFSO = the PDS stock held across that mandal's shops. Shop locations
are not published, so the pickup point is a point inside the mandal's official OSM boundary,
matched by name (unique matches only; unmatched AFSOs are skipped and counted, never guessed).
This is government ration stock: the supplier name says so, and the Brain's approval gate shows
it to the human approver.
"""

from __future__ import annotations

import collections
from datetime import datetime
from zoneinfo import ZoneInfo

import asyncpg
import httpx
from reliefroute_contracts.enums import ItemCode

from app.core.logging import get_logger
from app.core.resilience import CircuitBreaker, SourceUnavailable, resilient_get

from ..area_names import normalize
from ..base import FetchResult, SupplierStock

log = get_logger(__name__)
URL = "https://aepos.ap.gov.in/EposLogin/api/stock/details"
IST = ZoneInfo("Asia/Kolkata")
COMMODITIES = {2: ItemCode.RICE_KG, 5: ItemCode.PULSES_KG}  # AePDS commCode -> item (2 Rice KG, 5 Redgram Dal KG)
UNIT_KG = {"KG": 1.0}

MANDAL_SQL = """
WITH state AS (SELECT geom FROM admin_areas WHERE admin_level = 4 AND name ILIKE 'Andhra Pradesh' LIMIT 1)
SELECT a.name_norm, count(*) OVER (PARTITION BY a.name_norm) AS n, ST_X(ST_PointOnSurface(a.geom)) AS lon,
       ST_Y(ST_PointOnSurface(a.geom)) AS lat, a.source_ref
FROM admin_areas a, state WHERE a.admin_level = 6 AND ST_Within(ST_PointOnSurface(a.geom), state.geom)
"""


def afso_key(name: str) -> str:
    return normalize(name.replace("(R)", "").replace("(U)", ""))


def aggregate(rows: list[dict], item: ItemCode) -> dict[tuple[str, str], dict]:
    """Sum closing balances (cb) per (district, AFSO) across shops and schemes; KG only."""
    out: dict[tuple[str, str], dict] = collections.defaultdict(lambda: {"kg": 0.0, "shops": set(), "as_of": None})
    for r in rows:
        unit = UNIT_KG.get(str(r.get("commMeasureUnit", "")).upper())
        if unit is None or not r.get("cb"):
            continue
        agg = out[(r["distName"].strip(), r["afsoName"].strip())]
        agg["kg"] += float(r["cb"]) * unit
        agg["shops"].add(r["fpsId"])
        agg["as_of"] = max(filter(None, [agg["as_of"], r.get("dataTime")]), default=None)
    return {k: v for k, v in out.items() if v["kg"] > 0}


class AepdsSource:
    name = "aepds"
    ttl_s = 24 * 3600

    def __init__(self, poll_s: int):
        self.poll_s = poll_s
        self.breaker = CircuitBreaker("aepds", failure_threshold=3, reset_after_s=1800)

    async def fetch(self, http: httpx.AsyncClient, conn: asyncpg.Pool) -> FetchResult:
        mandals = {r["name_norm"]: r for r in await conn.fetch(MANDAL_SQL) if r["n"] == 1}
        if not mandals:
            raise SourceUnavailable(self.name, "no mandal boundaries loaded (osm_extract source must run first)")
        now = datetime.now(IST)
        result: list[SupplierStock] = []
        unmatched: dict[str, int] = {}
        for code, item in COMMODITIES.items():
            resp = await resilient_get(http, URL, self.breaker, params={"month": now.month, "year": now.year, "commCode": code},
                                       timeout_s=900, retries=1)  # fmt: skip
            if resp.status_code != 200:
                raise SourceUnavailable(self.name, f"commCode {code}: HTTP {resp.status_code}")
            groups = aggregate(resp.json(), item)
            missed = 0
            for (district, afso), agg in groups.items():
                m = mandals.get(afso_key(afso))
                if m is None:
                    missed += 1
                    continue
                result.append(SupplierStock(
                    external_ref=f"aepds:{district}:{afso}", supplier_name=f"AP Civil Supplies PDS stock, {afso} ({district})",
                    site_name=f"PDS fair-price shops of {afso} mandal", lon=m["lon"], lat=m["lat"], item=item,
                    quantity=round(agg["kg"], 1), unit_weight_kg=1.0,
                    reported_at=datetime.fromisoformat(agg["as_of"]) if agg["as_of"] else now,
                    attributes={"shops": len(agg["shops"]), "district": district, "afso": afso,
                                "location_basis": f"point inside OSM mandal boundary {m['source_ref']}; shop locations not published",
                                "source": URL},
                ))  # fmt: skip
            unmatched[item.value] = missed
            log.info("aepds_commodity", item=item.value, afsos=len(groups), matched=len(groups) - missed, unmatched=missed)
        return FetchResult(hazards=[], facilities=[], supplier_stock=result, skipped=sum(unmatched.values()))
