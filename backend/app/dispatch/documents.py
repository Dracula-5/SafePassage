"""Purchase-order PDFs (reportlab). Content comes only from the persisted order."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.agents.schemas import OrderDraft


def purchase_order_pdf(path: Path, plan_id: str, order: OrderDraft, destination_name: str, idempotency_key: str,
                       is_drill: bool, issued_at: datetime) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    story = [
        Paragraph(("DRILL - NOT A REAL ORDER. " if is_drill else "") + "ReliefRoute purchase order", styles["Title"]),
        Paragraph(f"Order key: {order.order_key} &nbsp; Idempotency key: {idempotency_key}", styles["Normal"]),
        Paragraph(f"Plan: {plan_id} &nbsp; Issued: {issued_at.isoformat(timespec='minutes')}", styles["Normal"]),
        Spacer(1, 12),
        Paragraph(f"Supplier: {order.supplier_name}{'' if order.supplier_verified else ' (unverified)'}", styles["Normal"]),
        Paragraph(f"Deliver to: {destination_name}", styles["Normal"]),
        Spacer(1, 12),
    ]
    rows = [["Item", "Quantity"]] + [[line.item_code.value, f"{line.quantity:,.2f}"] for line in order.lines]
    rows.append(["Total weight (kg, supplier-declared unit weights)", f"{order.weight_kg:,.1f}"])
    table = Table(rows, colWidths=[300, 150])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)]))  # fmt: skip
    story.append(table)
    SimpleDocTemplate(str(path), pagesize=A4, title=f"PO {order.order_key}").build(story)
    return path
