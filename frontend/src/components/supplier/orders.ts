// What the supplier screens say about an order. Only the statuses the backend really has are used.

import { SupplierOrder } from "@/lib/api";
import { itemName } from "@/lib/format";

export type Tone = "good" | "warn" | "info" | "plain" | "bad";

export const ORDER_STATUS: Record<string, { text: string; tone: Tone }> = {
  draft: { text: "Pending", tone: "plain" },
  approved: { text: "Approved", tone: "good" },
  dispatched: { text: "Prepare for pickup", tone: "warn" },
  in_transit: { text: "On the truck", tone: "info" },
  delivered: { text: "Delivered", tone: "good" },
  cancelled: { text: "Cancelled", tone: "bad" },
};

export function statusOf(status: string): { text: string; tone: Tone } {
  return ORDER_STATUS[status] ?? { text: status.replace(/_/g, " "), tone: "plain" };
}

export const FILTERS = [
  { key: "all", label: "All", statuses: null },
  { key: "pending", label: "Pending", statuses: ["draft", "approved"] },
  { key: "dispatched", label: "Dispatched", statuses: ["dispatched", "in_transit"] },
  { key: "completed", label: "Completed", statuses: ["delivered"] },
] as const;

export type FilterKey = (typeof FILTERS)[number]["key"];

export function inFilter(order: SupplierOrder, key: FilterKey): boolean {
  const statuses = FILTERS.find((f) => f.key === key)?.statuses;
  return !statuses || (statuses as readonly string[]).includes(order.status);
}

/** Still needs something from the supplier or is on its way: not delivered and not cancelled. */
export function isOpen(order: SupplierOrder): boolean {
  return order.status !== "delivered" && order.status !== "cancelled";
}

/** Short reference taken from the real order id. */
export function orderRef(id: string): string {
  return `#${id.slice(0, 8).toUpperCase()}`;
}

export function dateText(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

export function dateTimeText(iso: string): string {
  return `${dateText(iso)}, ${new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}`;
}

export function quantityText(quantity: number, unit?: string): string {
  return `${Math.round(quantity).toLocaleString()}${unit ? ` ${unit}` : ""}`;
}

export function lineSummary(order: SupplierOrder): string {
  return order.lines.map((l) => `${quantityText(l.quantity)} ${itemName(l.item_code)}`).join(", ");
}
