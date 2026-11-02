"""Stock writes shared by the web portal and the WhatsApp webhook."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import asyncpg
from pydantic import BaseModel, Field
from reliefroute_contracts.enums import ItemCode, StockReportChannel
from reliefroute_contracts.events import SupplierStockUpdated
from reliefroute_contracts.streams import SUPPLIER_STOCK_UPDATED

from app.core import bus


class StockLine(BaseModel):
    item_code: ItemCode
    quantity: float = Field(ge=0, le=1e9)
    unit_weight_kg: float = Field(gt=0, le=10_000)


async def report_stock(
    conn: asyncpg.Connection, supplier_id: UUID, lines: list[StockLine], channel: StockReportChannel
) -> list[SupplierStockUpdated]:
    """Upsert stock rows in the caller's transaction; returns the events to publish after commit."""
    now = datetime.now(UTC)
    events = []
    for line in lines:
        previous = await conn.fetchval(
            "SELECT quantity FROM stock WHERE supplier_id = $1 AND item_code = $2", supplier_id, line.item_code.value)
        await conn.execute(
            """INSERT INTO stock (supplier_id, item_code, quantity, unit_weight_kg, reported_at, reported_via)
               VALUES ($1, $2, $3, $4, $5, $6)
               ON CONFLICT (supplier_id, item_code) DO UPDATE SET quantity = EXCLUDED.quantity,
                   unit_weight_kg = EXCLUDED.unit_weight_kg, reported_at = EXCLUDED.reported_at,
                   reported_via = EXCLUDED.reported_via, updated_at = now()""",
            supplier_id, line.item_code.value, line.quantity, line.unit_weight_kg, now, channel.value,
        )  # fmt: skip
        events.append(SupplierStockUpdated(
            supplier_id=supplier_id, item_code=line.item_code, quantity=line.quantity, previous_quantity=previous,
            reported_at=now, reported_via=channel,
        ))  # fmt: skip
    return events


async def publish_stock_events(events: list[SupplierStockUpdated]) -> None:
    """The stock is committed and planning reads it from the table, so a Redis outage loses nothing."""
    for e in events:
        await bus.try_publish(SUPPLIER_STOCK_UPDATED, e, dedupe_key=f"{e.supplier_id}:{e.item_code}:{e.reported_at.isoformat()}")


def parse_whatsapp_stock(body: str) -> list[StockLine]:
    """'WATER_L 5000 1; RICE_KG 800 1' -> lines (item, quantity, unit weight kg). Raises ValueError."""
    lines = []
    for chunk in body.replace("\n", ";").split(";"):
        parts = chunk.split()
        if not parts:
            continue
        if len(parts) != 3:
            raise ValueError(f"expected 'ITEM QUANTITY UNIT_WEIGHT_KG', got {chunk.strip()!r}")
        lines.append(StockLine(item_code=parts[0].upper(), quantity=float(parts[1]), unit_weight_kg=float(parts[2])))
    if not lines:
        raise ValueError("no stock lines found")
    return lines
