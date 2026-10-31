"""Supplier portal under /portal/*.

- GET  /portal                  check-in page (register + report stock), plain HTML/JS
- GET  /portal/api/facilities   facility search for registration (name, near a point)
- POST /portal/api/register     create a supplier at an existing facility or at its GPS location
- POST /portal/api/stock        contract endpoint; X-Supplier-Token; emits supplier.stock.updated
- POST /portal/whatsapp         Twilio-compatible webhook, disabled unless TWILIO_AUTH_TOKEN is set
- POST /api/suppliers/{id}/verify  operator decision: vouch for a supplier, or withdraw that and its token

New suppliers start unverified; any plan using one needs a human, who sees that.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Annotated
from urllib.parse import parse_qsl
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field, model_validator
from reliefroute_contracts.enums import ITEM_UNITS, StockReportChannel

from app.core.auth import require_operator
from app.core.db import get_pool
from app.core.security import CSP
from app.core.validation import Name

from ..ingestion.settings import get_senses_settings
from ..telemetry.tokens import hash_token, new_token
from .page import PORTAL_HTML
from .service import StockLine, parse_whatsapp_stock, publish_stock_events, report_stock

router = APIRouter(tags=["senses"])


@router.get("/portal", response_class=HTMLResponse)
async def portal_page() -> HTMLResponse:
    items = "".join(f'<option value="{code.value}">{code.value} ({unit})</option>' for code, unit in ITEM_UNITS.items())
    nonce = secrets.token_urlsafe(16)  # the page's one inline script runs; anything injected does not
    html = PORTAL_HTML.replace("{{ITEM_OPTIONS}}", items).replace("{{NONCE}}", nonce)
    return HTMLResponse(html, headers={"Content-Security-Policy": CSP.replace("script-src 'self'", f"script-src 'nonce-{nonce}'")})


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


@router.post("/portal/api/register", status_code=201)
async def register_supplier(body: Registration) -> dict:
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
                                      portal_token_hash) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
            facility_id, body.name, body.contact_phone, body.contact_whatsapp, body.preferred_language, digest,
        )  # fmt: skip
    return {"supplier_id": str(supplier_id), "facility_id": str(facility_id), "supplier_token": token, "verified": False}


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


@router.post("/portal/api/stock")
async def post_stock(body: StockReport, x_supplier_token: Annotated[str, Header()]) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        supplier_id = await conn.fetchval("SELECT id FROM suppliers WHERE portal_token_hash = $1", hash_token(x_supplier_token))
        if supplier_id is None:
            raise HTTPException(401, "unknown supplier token")
        events = await report_stock(conn, supplier_id, body.lines, StockReportChannel.PORTAL)
        rows = await conn.fetch(
            """SELECT supplier_id, item_code::text, quantity, reserved, available, unit_weight_kg, reported_at
               FROM v_available_stock WHERE supplier_id = $1 ORDER BY item_code""", supplier_id)
    await publish_stock_events(events)
    return [dict(r) | {"supplier_id": str(r["supplier_id"]), "reported_at": r["reported_at"].isoformat()} for r in rows]


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
