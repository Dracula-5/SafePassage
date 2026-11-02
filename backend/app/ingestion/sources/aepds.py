"""Andhra Pradesh ePoS (AePDS) public stock report: live closing balances of PDS commodities at
every fair-price shop (FPS), by district and mandal supply office (AFSO).

Endpoint: GET https://aepos.ap.gov.in/EposLogin/api/stock/details?month&year&commCode (public,
used by the portal's own report page; no captcha or login). Responses are large (~50 MB for
rice, minutes to serve), so commodities are fetched one at a time with long timeouts, every
few hours.

Mapping: one supplier per AFSO = the PDS stock held across that mandal's shops. Shop locations
are not published, so the pickup point is a point inside the mandal's official OSM boundary. AePDS
and OSM romanise Telugu names differently, so offices are matched in two passes (see
match_offices). A wrong match would put real stock in the wrong town, so an office with no single
answer is skipped and counted, never guessed.
This is government ration stock: the supplier name says so, and the Brain's approval gate shows
it to the human approver.
"""

from __future__ import annotations

import collections
import re
from collections.abc import Iterable
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
SELECT a.name_norm, ST_X(p.pt) AS lon, ST_Y(p.pt) AS lat, a.source_ref, d.name_norm AS district
FROM admin_areas a CROSS JOIN state
CROSS JOIN LATERAL (SELECT ST_PointOnSurface(a.geom) AS pt) p
LEFT JOIN LATERAL (SELECT name_norm FROM admin_areas x WHERE x.admin_level = 5 AND ST_Contains(x.geom, p.pt) LIMIT 1) d ON true
WHERE a.admin_level = 6 AND ST_Within(p.pt, state.geom)
"""
URBAN_OFFICE = re.compile(r"\(U\)|\b(urban|urb|circle|aso office)\b", re.I)
RURAL_OFFICE = re.compile(r"\(R\)|\brural\b", re.I)
OFFICE_WORDS = re.compile(r"\(U\)|\(R\)|\b(urban|urb|rural|circle|aso|office)\b|\b[ivx]+\b", re.I)


def afso_keys(name: str, district: str = "") -> list[str]:
    """Mandal names to try, best first. A town's supply offices are named many ways ('Vijayawada (U)',
    'Eluru Urban', 'Vijayawada Urban-Ii', 'Circle I Urban'); they lie in its urban mandal, or in
    the single mandal of a smaller town. 'Gnt Urban' names no place, so the district is tried too."""
    base = normalize(OFFICE_WORDS.sub(" ", name.replace("-", " ")))
    if RURAL_OFFICE.search(name):
        return [f"{base} rural", base]
    if not URBAN_OFFICE.search(name):
        return [base]
    town = normalize(district)
    return [key for key in (f"{base} urban", base, f"{town} urban", town) if key.strip()]


def spelling_key(name: str) -> str:
    """One form for the ways Telugu names are romanised: Tadepalli/Tadepalle, Kaikaluru/Kaikalur,
    Thallapudi/Tallapudi, Dwaraka Tirumala/Dwarakatirumala."""
    key = name.replace(" ", "")
    for variant, plain in (("th", "t"), ("dh", "d"), ("w", "v"), ("y", "i"), ("ee", "i"), ("oo", "u")):
        key = key.replace(variant, plain)
    key = re.sub(r"(.)\1+", r"\1", key)                      # Yadamari/Yadamarri
    key = re.sub(r"pal(i|e|em)$", "pal", key)                 # -palli, -palle
    key = re.sub(r"(pet|peta)$", "pet", key)                  # Mandapet/Mandapeta
    return re.sub(r"(ur|uru|oru|oor)$", "ur", key)            # Giddaluru/Giddalur


def match_offices(offices: Iterable[tuple[str, str]], mandals: list) -> dict[tuple[str, str], tuple[object, str]]:
    """(district, office) -> (mandal row, how it matched). Offices with no single answer are absent.

    First pass: a name that exists exactly once in the state. Those matches also show which OSM
    districts each AePDS district lies in, with no table to maintain (the state keeps redrawing
    districts). Second pass: the rest, by exact name or spelling, but only inside those districts.
    That stops 'Kovur' (Nellore) landing on 'Kovvur' (East Godavari).
    """
    by_name: dict[str, list] = collections.defaultdict(list)
    by_spelling: dict[str, list] = collections.defaultdict(list)
    for row in mandals:
        by_name[row["name_norm"]].append(row)
        by_spelling[spelling_key(row["name_norm"])].append(row)

    found: dict[tuple[str, str], tuple[object, str]] = {}
    districts: dict[str, set] = collections.defaultdict(set)
    waiting = []
    for office in offices:
        unique = next((by_name[key][0] for key in afso_keys(office[1], office[0]) if len(by_name[key]) == 1), None)
        if unique is None:
            waiting.append(office)
        else:
            found[office] = (unique, "exact name")
            districts[office[0]].add(unique["district"])
    for office in waiting:
        for key in afso_keys(office[1], office[0]):
            exact = [r for r in by_name[key] if r["district"] in districts[office[0]]]
            spelled = [r for r in by_spelling[spelling_key(key)] if r["district"] in districts[office[0]]]
            if len(exact) == 1:
                found[office] = (exact[0], "exact name in its district")
                break
            if len(spelled) == 1:
                found[office] = (spelled[0], "spelling variant in its district")
                break
    return found


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
        mandals = await conn.fetch(MANDAL_SQL)
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
            matches = match_offices(groups, mandals)
            missed = 0
            missing_names: list[str] = []
            for (district, afso), agg in groups.items():
                if (district, afso) not in matches:
                    missed += 1
                    missing_names.append(f"{district}/{afso}")
                    continue
                m, how = matches[(district, afso)]
                result.append(SupplierStock(
                    external_ref=f"aepds:{district}:{afso}", supplier_name=f"AP Civil Supplies PDS stock, {afso} ({district})",
                    site_name=f"PDS fair-price shops of {afso} mandal", lon=m["lon"], lat=m["lat"], item=item,
                    quantity=round(agg["kg"], 1), unit_weight_kg=1.0,
                    reported_at=datetime.fromisoformat(agg["as_of"]) if agg["as_of"] else now,
                    attributes={"shops": len(agg["shops"]), "district": district, "afso": afso,
                                "location_basis": f"point inside OSM mandal boundary {m['source_ref']} ({how}); "
                                                  "shop locations not published",
                                "source": URL},
                ))  # fmt: skip
            unmatched[item.value] = missed
            log.info("aepds_commodity", item=item.value, afsos=len(groups), matched=len(groups) - missed, unmatched=missed,
                     unmatched_names=sorted(missing_names))
        return FetchResult(hazards=[], facilities=[], supplier_stock=result, skipped=sum(unmatched.values()))
