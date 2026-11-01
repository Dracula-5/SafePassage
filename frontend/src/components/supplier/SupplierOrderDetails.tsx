// One order. The progress shown comes only from the order's real status and the two timestamps the API has:
// created_at and delivered_at. Pickup, transit, driver and GPS data are not in this API, so none is shown.

import { SupplierOrder } from "@/lib/api";
import { itemName } from "@/lib/format";

import Icon, { itemIcon } from "./icons";
import { dateText, dateTimeText, orderRef, quantityText, statusOf } from "./orders";
import styles from "./supplier.module.css";
import { DrillBadge, EmptyState, Notice, PageHeader, StatusBadge } from "./ui";

const RANK: Record<string, number> = { draft: 0, approved: 1, dispatched: 2, in_transit: 3, delivered: 4 };

function steps(order: SupplierOrder) {
  const rank = RANK[order.status] ?? 0;
  return [
    { title: "Order created", note: dateTimeText(order.created_at), done: true },
    { title: "Plan approved", note: rank >= 1 ? "Approved by a duty officer" : "Waiting for approval", done: rank >= 1 },
    { title: "Pickup (prepare stock)", note: rank >= 2 ? "Dispatched: have the goods ready" : "Not dispatched yet", done: rank >= 2 },
    { title: "In transit", note: rank >= 3 ? "Goods are on the truck" : "Not started", done: rank >= 3 },
    { title: "Delivered", note: order.delivered_at ? dateTimeText(order.delivered_at) : rank >= 4 ? "Delivered" : "Not delivered yet", done: rank >= 4 },
  ];
}

export default function SupplierOrderDetails({ order, units }: { order: SupplierOrder | undefined; units: Record<string, string> }) {
  if (!order) {
    return (
      <>
        <PageHeader title="Order" backTo={{ name: "orders" }} />
        <EmptyState icon="orders" title="Order not found">It may belong to another account, or has not loaded yet. Go back to your orders.</EmptyState>
      </>
    );
  }
  const status = statusOf(order.status);
  const cancelled = order.status === "cancelled";
  const list = steps(order);
  const current = list.findIndex((s) => !s.done);

  return (
    <>
      <PageHeader title={`Order ${orderRef(order.id)}`} backTo={{ name: "orders" }} action={<StatusBadge tone={status.tone}>{status.text}</StatusBadge>} />
      <div className={styles.twoCol}>
        <div className={styles.stack}>
          <div className={styles.orderHero}>
            <span className={styles.badgeRow}>{order.is_drill && <DrillBadge />}</span>
            <h2>{order.destination}</h2>
            <div className={styles.orderHeroMeta}>
              <span><Icon name="pin" size={18} /> Destination: {order.destination}</span>
              <span><Icon name="calendar" size={18} /> Ordered {dateText(order.created_at)}</span>
              <span><Icon name="box" size={18} /> {order.lines.length} {order.lines.length === 1 ? "item" : "items"}</span>
            </div>
          </div>
          {order.is_drill && <Notice tone="warn">This is a drill. No goods are expected to move.</Notice>}
          <section className={styles.card} aria-labelledby="lines-h">
            <h2 id="lines-h">Order Details</h2>
            <ul className={styles.detailList}>
              {order.lines.map((l) => (
                <li key={l.item_code} className={styles.detailRow}>
                  <span className={styles.bubble}><Icon name={itemIcon(l.item_code)} /></span>
                  <span>{itemName(l.item_code)}{units[l.item_code] ? ` (${units[l.item_code]})` : ""}</span>
                  <b>{quantityText(l.quantity, units[l.item_code])}</b>
                </li>
              ))}
            </ul>
          </section>
        </div>

        <section className={styles.card} aria-labelledby="progress-h">
          <h2 id="progress-h">Delivery Progress</h2>
          {cancelled ? <Notice tone="bad">This order was cancelled.</Notice> : (
            <ol className={styles.timeline}>
              {list.map((s, i) => (
                <li key={s.title} className={`${styles.step} ${s.done ? styles.stepDone : i === current ? styles.stepNow : styles.stepWait}`} aria-current={i === current ? "step" : undefined}>
                  <span className={styles.stepDot}>{s.done && <Icon name="check" size={15} />}</span>
                  <span className={styles.stepText}><b>{s.title}</b><span>{s.note}</span></span>
                </li>
              ))}
            </ol>
          )}
          <p className={`${styles.muted} ${styles.small}`}>Pickup and transit times, drivers and truck positions are not recorded for suppliers, so only the status is shown.</p>
        </section>
      </div>
    </>
  );
}
