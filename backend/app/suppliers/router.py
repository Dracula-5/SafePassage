"""Supplier check-in. The screens are in the web app, at /supplier.

- GET  /portal/api/facilities   facility search for registration (name, near a point)
- POST /portal/api/register     create a supplier at an existing facility or at its GPS location
- POST /portal/api/stock        contract endpoint; X-Supplier-Token; emits supplier.stock.updated
- GET, POST /api/supplier/me    the signed-in supplier account's own organisation
- POST /api/supplier/stock      the same stock report, for the signed-in account
- GET  /api/supplier/orders     what dispatched plans have asked this supplier to prepare
- POST /portal/whatsapp         Twilio-compatible webhook, disabled unless TWILIO_AUTH_TOKEN is set
- GET  /api/suppliers           operator: every supplier and its stock
- POST /api/suppliers/{id}/verify  operator decision: vouch for a supplier, or withdraw that and its token

New suppliers start unverified; any plan using one needs a human, who sees that.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Annotated
from urllib.parse import parse_qsl
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from reliefroute_contracts.enums import ITEM_UNITS, StockReportChannel

from app.core.auth import require_operator, require_role
from app.core.db import get_pool
from app.core.validation import Name

from ..ingestion.settings import get_senses_settings
from ..telemetry.tokens import hash_token, new_token
from .service import StockLine, parse_whatsapp_stock, publish_stock_events, report_stock

router = APIRouter(tags=["senses"])
MAX_SUPPLIER_ORDERS = 50


Supplier = Annotated[dict, Depends(require_role("supplier"))]


def _in_region(lon: float, lat: float) -> bool:
    x0, y0, x1, y1 = get_senses_settings().region_bbox
    return x0 <= lon <= x1 and y0 <= lat <= y1


def _like(text: str) -> str:
    """Escape LIKE's wildcards: a search for "%" should find a percent sign, not every row."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/portal/api/facilities")
async def search_facilities(
    q: Annotated[str, Query(min_length=2, max_length=80)],
    lon: float | None = Query(default=None, ge=-180, le=180),
    lat: float | None = Query(default=None, ge=-90, le=90),
) -> list[dict]:
    rows = await (await get_pool()).fetch(
        """SELECT id, name, kind::text, ST_X(geom) AS lon, ST_Y(geom) AS lat,
                  CASE WHEN $2::float8 IS NULL THEN NULL
                       ELSE ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint($2, $3::float8), 4326)::geography) END AS m
           FROM facilities WHERE name ILIKE '%' || $1 || '%'
           ORDER BY m NULLS LAST, name LIMIT 20""",
        _like(q), lon, lat,
    )  # fmt: skip
    return [{"id": str(r["id"]), "name": r["name"], "kind": r["kind"], "distance_m": r["m"]} for r in rows]


class Registration(BaseModel):
    name: Name = Field(min_length=2, max_length=120)
    contact_phone: str | None = Field(default=None, pattern=r"^\+[1-9]\d{6,14}$")
    contact_whatsapp: str | None = Field(default=None, pattern=r"^\+[1-9]\d{6,14}$")
    preferred_language: str = Field(default="en", pattern=r"^[a-z]{2,3}$")
    facility_id: UUID | None = None
    # Or register a new site at the supplier's own GPS position:
    lon: float | None = Field(default=None, ge=-180, le=180)
    lat: float | None = Field(default=None, ge=-90, le=90)
    site_name: Name | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _one_location(self) -> "Registration":
        if (self.facility_id is None) == (self.lon is None or self.lat is None):
            raise ValueError("give either facility_id or lon+lat")
        return self


async def _create_supplier(body: Registration, account_id: UUID | None = None) -> dict:
    if body.lon is not None and not _in_region(body.lon, body.lat):
        raise HTTPException(422, f"that location is outside the mapped region ({get_senses_settings().region_name})")
    token, digest = new_token()
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        facility_id = body.facility_id
        if facility_id is not None:
            if not await conn.fetchval("SELECT 1 FROM facilities WHERE id = $1", facility_id):
                raise HTTPException(422, "facility_id does not exist")
        else:
            facility_id = await conn.fetchval(
                """INSERT INTO facilities (source, source_ref, kind, name, geom, fetched_at)
                   VALUES ('portal', 'portal:' || gen_random_uuid(), 'warehouse', $1,
                           ST_SetSRID(ST_MakePoint($2, $3), 4326), now()) RETURNING id""",
                body.site_name or body.name, body.lon, body.lat,
            )  # fmt: skip
        supplier_id = await conn.fetchval(
            """INSERT INTO suppliers (facility_id, name, contact_phone, contact_whatsapp, preferred_language,
                                      portal_token_hash, account_id) VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id""",
            facility_id, body.name, body.contact_phone, body.contact_whatsapp, body.preferred_language, digest, account_id,
        )  # fmt: skip
    return {"supplier_id": str(supplier_id), "facility_id": str(facility_id), "supplier_token": token, "verified": False}


@router.post("/portal/api/register", status_code=201)
async def register_supplier(body: Registration) -> dict:
    return await _create_supplier(body)


async def _stock_levels(conn, supplier_id: UUID) -> list[dict]:
    rows = await conn.fetch(
        """SELECT supplier_id, item_code::text, quantity, reserved, available, unit_weight_kg, reported_at
           FROM v_available_stock WHERE supplier_id = $1 ORDER BY item_code""", supplier_id)
    return [dict(r) | {"supplier_id": str(r["supplier_id"]), "reported_at": r["reported_at"].isoformat()} for r in rows]


@router.get("/api/supplier/me")
async def my_supplier(account: Supplier) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT s.id, s.name, s.verified, f.name AS site FROM suppliers s JOIN facilities f ON f.id = s.facility_id
               WHERE s.account_id = $1""", account["id"])  # fmt: skip
        if row is None:
            raise HTTPException(404, "no organisation registered yet")
        return {"id": str(row["id"]), "name": row["name"], "site": row["site"], "verified": row["verified"],
                "stock": await _stock_levels(conn, row["id"]),
                "items": [{"code": code.value, "unit": unit} for code, unit in ITEM_UNITS.items()]}  # fmt: skip


@router.post("/api/supplier/me", status_code=201)
async def register_my_supplier(body: Registration, account: Supplier) -> dict:
    if await (await get_pool()).fetchval("SELECT 1 FROM suppliers WHERE account_id = $1", account["id"]):
        raise HTTPException(409, "this account already has an organisation")
    created = await _create_supplier(body, account["id"])
    return {"supplier_id": created["supplier_id"], "verified": False}


@router.get("/api/suppliers", dependencies=[Depends(require_operator)])
async def list_suppliers() -> list[dict]:
    """Every supplier and what it holds, for the Directory. Most have no account: they come from AePDS."""
    rows = await (await get_pool()).fetch(
        """SELECT s.id, s.name, s.verified, s.contact_phone, s.contact_whatsapp, s.account_id IS NOT NULL AS has_account,
                  f.name AS site, max(st.reported_at) AS reported_at,
                  COALESCE(json_agg(json_build_object('item_code', st.item_code, 'available', st.available, 'reserved', st.reserved)
                                    ORDER BY st.item_code) FILTER (WHERE st.supplier_id IS NOT NULL), '[]')::text AS stock
           FROM suppliers s JOIN facilities f ON f.id = s.facility_id LEFT JOIN v_available_stock st ON st.supplier_id = s.id
           GROUP BY s.id, f.name ORDER BY s.account_id IS NULL, s.name""")  # fmt: skip
    return [{"id": str(r["id"]), "name": r["name"], "site": r["site"], "verified": r["verified"], "has_account": r["has_account"],
             "contact": r["contact_whatsapp"] or r["contact_phone"], "stock": json.loads(r["stock"]),
             "reported_at": r["reported_at"].isoformat() if r["reported_at"] else None} for r in rows]  # fmt: skip


class SupplierDecision(BaseModel):
    verified: bool


@router.post("/api/suppliers/{supplier_id}/verify", dependencies=[Depends(require_operator)])
async def verify_supplier(supplier_id: UUID, body: SupplierDecision) -> dict:
    """Withdrawing verification also ends the token: the usual reason is that it can't be trusted."""
    updated = await (await get_pool()).fetchval(
        """UPDATE suppliers SET verified = $2, updated_at = now(),
               portal_token_hash = CASE WHEN $2 THEN portal_token_hash END
           WHERE id = $1 RETURNING id""", supplier_id, body.verified)  # fmt: skip
    if updated is None:
        raise HTTPException(404, "supplier not found")
    return {"id": str(supplier_id), "verified": body.verified}


class StockReport(BaseModel):
    lines: list[StockLine] = Field(min_length=1, max_length=len(ITEM_UNITS))

    @model_validator(mode="after")
    def _unique_items(self) -> "StockReport":
        if len({line.item_code for line in self.lines}) != len(self.lines):
            raise ValueError("each item_code may appear once")
        return self


async def _report(body: StockReport, conn, supplier_id: UUID) -> list[dict]:
    events = await report_stock(conn, supplier_id, body.lines, StockReportChannel.PORTAL)
    levels = await _stock_levels(conn, supplier_id)
    return events, levels


@router.post("/portal/api/stock")
async def post_stock(body: StockReport, x_supplier_token: Annotated[str, Header()]) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        supplier_id = await conn.fetchval("SELECT id FROM suppliers WHERE portal_token_hash = $1", hash_token(x_supplier_token))
        if supplier_id is None:
            raise HTTPException(401, "unknown supplier token")
        events, levels = await _report(body, conn, supplier_id)
    await publish_stock_events(events)
    return levels


@router.post("/api/supplier/stock")
async def post_my_stock(body: StockReport, account: Supplier) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        supplier_id = await conn.fetchval("SELECT id FROM suppliers WHERE account_id = $1", account["id"])
        if supplier_id is None:
            raise HTTPException(404, "no organisation registered yet")
        events, levels = await _report(body, conn, supplier_id)
    await publish_stock_events(events)
    return levels


@router.get("/api/supplier/orders")
async def my_orders(account: Supplier) -> list[dict]:
    rows = await (await get_pool()).fetch(
        """SELECT o.id, o.status::text, o.created_at, o.delivered_at, p.is_drill, f.name AS destination,
                  json_agg(json_build_object('item_code', l.item_code, 'quantity', l.quantity) ORDER BY l.item_code)::text AS lines
           FROM orders o JOIN suppliers s ON s.id = o.supplier_id JOIN plans p ON p.id = o.plan_id
           JOIN facilities f ON f.id = o.destination_facility_id JOIN order_lines l ON l.order_id = o.id
           WHERE s.account_id = $1
           GROUP BY o.id, p.is_drill, f.name ORDER BY o.created_at DESC LIMIT $2""",
        account["id"], MAX_SUPPLIER_ORDERS,
    )  # fmt: skip
    return [{"id": str(r["id"]), "status": r["status"], "is_drill": r["is_drill"], "destination": r["destination"],
             "lines": json.loads(r["lines"]), "created_at": r["created_at"].isoformat(),
             "delivered_at": r["delivered_at"].isoformat() if r["delivered_at"] else None} for r in rows]  # fmt: skip


def twilio_signature_valid(auth_token: str, url: str, params: dict[str, str], signature: str) -> bool:
    """Twilio's documented scheme: HMAC-SHA1 over URL + sorted key/value pairs, base64."""
    data = url + "".join(k + params[k] for k in sorted(params))
    expected = base64.b64encode(hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest())
    return hmac.compare_digest(expected, signature.encode())  # bytes: a header can hold non-ASCII


def _twiml(message: str) -> Response:
    escaped = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return Response(f"<?xml version='1.0' encoding='UTF-8'?><Response><Message>{escaped}</Message></Response>",
                    media_type="application/xml")


@router.post("/portal/whatsapp")
async def whatsapp_webhook(request: Request, x_twilio_signature: Annotated[str | None, Header()] = None) -> Response:
    auth_token = get_senses_settings().twilio_auth_token
    if not auth_token:
        raise HTTPException(503, "WhatsApp check-in is disabled until TWILIO_AUTH_TOKEN is configured")
    # Twilio only sends urlencoded forms, so this needs neither request.form() nor python-multipart.
    form = dict(parse_qsl((await request.body()).decode(errors="replace"), keep_blank_values=True))
    if not x_twilio_signature or not twilio_signature_valid(auth_token, str(request.url), form, x_twilio_signature):
        raise HTTPException(403, "invalid Twilio signature")
    sender = form.get("From", "").removeprefix("whatsapp:")
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        supplier_id = await conn.fetchval("SELECT id FROM suppliers WHERE contact_whatsapp = $1", sender)
        if supplier_id is None:
            return _twiml("This number is not registered. Register at the ReliefRoute portal first.")
        try:
            lines = parse_whatsapp_stock(form.get("Body", ""))
        except ValueError:
            return _twiml("Could not read that. Send e.g. 'WATER_L 5000 1; RICE_KG 800 1'.")
        events = await report_stock(conn, supplier_id, lines, StockReportChannel.WHATSAPP)
    await publish_stock_events(events)
    return _twiml("Stock updated: " + ", ".join(f"{e.item_code.value} {e.quantity:g}" for e in events))
