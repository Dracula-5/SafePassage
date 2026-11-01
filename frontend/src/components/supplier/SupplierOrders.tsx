"use client";

// Orders that dispatched plans have asked this supplier to prepare. The API only returns the signed-in supplier's own.

import { useState } from "react";

import { SupplierOrder } from "@/lib/api";
import { itemName } from "@/lib/format";

import Icon from "./icons";
import { go } from "./nav";
import { dateText, FILTERS, FilterKey, inFilter, lineSummary, orderRef, statusOf } from "./orders";
import styles from "./supplier.module.css";
import { DrillBadge, EmptyState, PageHeader, StatusBadge } from "./ui";

export function SupplierOrderCard({ order }: { order: SupplierOrder }) {
  const status = statusOf(order.status);
  return (
    <button type="button" className={styles.orderCard} onClick={() => go({ name: "order", id: order.id })} aria-label={`Order ${orderRef(order.id)} to ${order.destination}, ${status.text}`}>
      <span className={styles.orderTop}>
        <b>{order.destination}</b>
        <span className={styles.badgeRow}>{order.is_drill && <DrillBadge />}<StatusBadge tone={status.tone}>{status.text}</StatusBadge></span>
      </span>
      <span className={styles.orderLines}>{lineSummary(order)}</span>
      <span className={styles.orderMeta}>
        <span>{orderRef(order.id)} · {dateText(order.created_at)}</span>
        <Icon name="chevron" size={18} />
      </span>
    </button>
  );
}

/** The same orders as a table, for wide screens. */
export function OrdersTable({ orders, units }: { orders: SupplierOrder[]; units: Record<string, string> }) {
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead><tr><th>Goods</th><th>Going to</th><th>Status</th><th>Ordered</th></tr></thead>
        <tbody>
          {orders.map((o) => {
            const status = statusOf(o.status);
            return (
              <tr key={o.id}>
                <td>
                  <button type="button" className={styles.rowBtn} onClick={() => go({ name: "order", id: o.id })}>
                    {o.lines.map((l) => `${Math.round(l.quantity).toLocaleString()} ${itemName(l.item_code)}${units[l.item_code] ? ` (${units[l.item_code]})` : ""}`).join(", ")}
                  </button>
                </td>
                <td>{o.destination}</td>
                <td><span className={styles.badgeRow}><StatusBadge tone={status.tone}>{status.text}</StatusBadge>{o.is_drill && <DrillBadge />}</span></td>
                <td className={styles.muted}>{dateText(o.created_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export const NO_ORDERS = "When a duty officer approves a plan that uses your stock, it will appear here.";

export default function SupplierOrders({ orders, units, desktop }: { orders: SupplierOrder[]; units: Record<string, string>; desktop: boolean }) {
  const [filter, setFilter] = useState<FilterKey>("all");
  const shown = orders.filter((o) => inFilter(o, filter));
  const label = FILTERS.find((f) => f.key === filter)!.label.toLowerCase();

  return (
    <>
      <PageHeader title="Orders" backTo={{ name: "home" }} backMobileOnly />
      <div className={styles.tabs} role="tablist" aria-label="Filter orders">
        {FILTERS.map((f) => (
          <button key={f.key} type="button" role="tab" aria-selected={filter === f.key} className={`${styles.tab} ${filter === f.key ? styles.tabOn : ""}`} onClick={() => setFilter(f.key)}>
            {f.label}<small>{orders.filter((o) => inFilter(o, f.key)).length}</small>
          </button>
        ))}
      </div>
      {orders.length === 0 ? <EmptyState icon="orders" title="No orders yet">{NO_ORDERS}</EmptyState>
        : shown.length === 0 ? <EmptyState icon="orders" title={`No ${label} orders`}>Orders with this status will appear here.</EmptyState>
        : desktop ? <section className={styles.card}><OrdersTable orders={shown} units={units} /></section>
        : <div className={styles.orderGrid}>{shown.map((o) => <SupplierOrderCard key={o.id} order={o} />)}</div>}
    </>
  );
}
