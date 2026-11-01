"use client";

// Home. Every number is worked out from the profile and orders the API returned: nothing is a stored or sample figure.

import { Account, SupplierOrder, SupplierProfile } from "@/lib/api";
import { ago } from "@/lib/format";

import Icon, { TruckArt } from "./icons";
import InventoryList from "./InventoryList";
import { go } from "./nav";
import { isOpen } from "./orders";
import styles from "./supplier.module.css";
import { EmptyState, Notice, SummaryCard, UNVERIFIED_NOTE, VerificationBadge } from "./ui";
import { NO_ORDERS, OrdersTable } from "./SupplierOrders";

export default function SupplierDashboard({ account, profile, orders, desktop }: {
  account: Account;
  profile: SupplierProfile;
  orders: SupplierOrder[];
  desktop: boolean;
}) {
  const units = Object.fromEntries(profile.items.map((i) => [i.code, i.unit]));
  const open = orders.filter(isOpen).length;
  const lastReported = profile.stock.reduce<string | null>((latest, s) => (!latest || s.reported_at > latest ? s.reported_at : latest), null);
  const recent = [...profile.stock].sort((a, b) => b.reported_at.localeCompare(a.reported_at)).slice(0, 3);

  const summary = (
    <div className={styles.summaryGrid}>
      <SummaryCard icon="orders" label="Open Orders" value={String(open)} onClick={() => go({ name: "orders" })} />
      <SummaryCard icon="boxes" tone="violet" label="Your Stock (items)" value={String(profile.stock.length)} onClick={() => go({ name: "inventory" })} />
      <SummaryCard icon="calendar" tone="cyan" label="Last Updated" value={lastReported ? ago(lastReported) : "—"} small onClick={() => go({ name: "inventory" })} />
      <SummaryCard icon="shield" tone="amber" label="Verification" value={profile.verified ? "Verified" : "Awaiting"} small onClick={() => go({ name: "profile" })} />
    </div>
  );

  const left = (
    <>
      <div className={styles.hero}>
        <div>
          <h2>Relief Supply Partner</h2>
          <p>Report stock, manage orders and support disaster response.</p>
        </div>
        <div className={styles.heroArt}><TruckArt /></div>
      </div>
      {summary}
      {!desktop && <button type="button" className={styles.primaryBtn} onClick={() => go({ name: "report" })}><Icon name="plus" size={20} /> Report Stock</button>}
    </>
  );

  return (
    <>
      <div className={styles.greetingRow}>
        <div className={styles.greeting}>
          <h1>Hi, {account.name}</h1>
          <p>{profile.site}</p>
        </div>
        <VerificationBadge verified={profile.verified} />
      </div>
      {!profile.verified && <Notice tone="warn">{UNVERIFIED_NOTE}</Notice>}

      <div className={styles.dashGrid}>
        <div className={styles.stack} style={{ gap: 16 }}>
          {left}
          {desktop ? (
            <section className={styles.card} aria-labelledby="orders-h">
              <div className={styles.sectionHead}>
                <h2 id="orders-h">Relief Orders</h2>
                {orders.length > 0 && <button type="button" className={styles.linkBtn} onClick={() => go({ name: "orders" })}>View All</button>}
              </div>
              {orders.length === 0
                ? <EmptyState icon="orders" title="No orders yet">{NO_ORDERS}</EmptyState>
                : <OrdersTable orders={orders.slice(0, 5)} units={units} />}
            </section>
          ) : (
            <button type="button" className={styles.shortcut} onClick={() => go({ name: "orders" })}>
              <span className={`${styles.bubble} ${styles.bubbleGood}`}><Icon name="truck" /></span>
              <span className={styles.shortcutText}>
                <b>Relief Orders</b>
                <span>{orders.length === 0 ? "No orders yet. Plans that use your stock will appear here." : `${open} open of ${orders.length} ${orders.length === 1 ? "order" : "orders"}`}</span>
              </span>
              <span className={styles.chev}><Icon name="chevron" size={18} /></span>
            </button>
          )}
        </div>

        <section className={styles.card} aria-labelledby="stock-h">
          <div className={styles.sectionHead}>
            <h2 id="stock-h">Recently Reported</h2>
            {profile.stock.length > 0 && <button type="button" className={styles.linkBtn} onClick={() => go({ name: "inventory" })}>View All</button>}
          </div>
          <InventoryList stock={recent} units={units} desktop={desktop} compact />
          {desktop && <button type="button" className={styles.primaryBtn} onClick={() => go({ name: "report" })}><Icon name="plus" size={18} /> Report Stock</button>}
        </section>
      </div>
    </>
  );
}
