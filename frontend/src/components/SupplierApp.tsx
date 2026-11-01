"use client";

// Supplier's screen: register an organisation once, then keep its real stock up to date. Planners only
// allocate what is reported here, so "reserved" moves by itself when a dispatch takes stock.
// The screens live in ./supplier; this file loads the data and picks which one to show.

import { useCallback, useEffect, useState } from "react";

import { ApiError, supplierApi, SupplierOrder, SupplierProfile } from "@/lib/api";
import { useAccount } from "@/lib/session";

import AppShell, { Loading } from "./AppShell";
import { REFRESH_MS } from "./LiveStatus";
import InventoryList from "./supplier/InventoryList";
import { DESKTOP, go, parseView, useHash, useMedia } from "./supplier/nav";
import Register from "./supplier/Register";
import ReportStockForm from "./supplier/ReportStockForm";
import styles from "./supplier/supplier.module.css";
import SupplierDashboard from "./supplier/SupplierDashboard";
import SupplierLayout from "./supplier/SupplierLayout";
import SupplierOrderDetails from "./supplier/SupplierOrderDetails";
import SupplierOrders from "./supplier/SupplierOrders";
import SupplierProfileView from "./supplier/SupplierProfile";
import Icon from "./supplier/icons";
import { LoadingSkeleton, PageHeader } from "./supplier/ui";

export default function SupplierApp() {
  const account = useAccount("supplier");
  const view = parseView(useHash());
  const desktop = useMedia(DESKTOP);
  const [profile, setProfile] = useState<SupplierProfile | null>(null);
  const [orders, setOrders] = useState<SupplierOrder[]>([]);
  const [unregistered, setUnregistered] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    try {
      setProfile(await supplierApi.me());
      setOrders(await supplierApi.orders());
      setUnregistered(false);
      setFailed(false);
      setUpdatedAt(Date.now());
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) setUnregistered(true);
      else setFailed(true);
    }
  }, []);

  useEffect(() => {
    if (!account) return;
    const first = setTimeout(load, 0);
    const timer = setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [account, load]);

  if (!account) return <Loading />;
  if (unregistered) {
    return (
      <AppShell account={account} title="Supplier">
        <main className="page"><Register account={account} onDone={load} /></main>
      </AppShell>
    );
  }
  if (!profile) {
    return (
      <div className={styles.root}>
        {failed ? (
          <main className={styles.content}>
            <div className={styles.error} role="alert">
              <Icon name="alert" size={36} />
              <h2>We could not load your portal</h2>
              <p className={styles.muted}>The server did not answer properly. Check your connection; this page keeps trying.</p>
              <button type="button" className={styles.ghostBtn} onClick={load}>Try again</button>
            </div>
          </main>
        ) : <LoadingSkeleton />}
      </div>
    );
  }

  const units = Object.fromEntries(profile.items.map((i) => [i.code, i.unit]));
  return (
    <SupplierLayout account={account} view={view} updatedAt={updatedAt} failed={failed}>
      {view.name === "home" && <SupplierDashboard account={account} profile={profile} orders={orders} desktop={desktop} />}
      {view.name === "orders" && <SupplierOrders orders={orders} units={units} desktop={desktop} />}
      {view.name === "order" && <SupplierOrderDetails order={orders.find((o) => o.id === view.id)} units={units} />}
      {view.name === "inventory" && (
        <>
          <PageHeader title="Inventory" backTo={{ name: "home" }} backMobileOnly
            action={desktop ? <button type="button" className={styles.primaryBtn} onClick={() => go({ name: "report" })}><Icon name="plus" size={18} /> Report Stock</button> : undefined} />
          <section className={desktop ? styles.card : undefined}>
            <InventoryList stock={profile.stock} units={units} desktop={desktop} />
          </section>
          {!desktop && <button type="button" className={styles.primaryBtn} onClick={() => go({ name: "report" })}><Icon name="plus" size={20} /> Report Stock</button>}
        </>
      )}
      {view.name === "report" && <ReportStockForm key={profile.id} profile={profile} desktop={desktop} onSaved={load} />}
      {view.name === "profile" && <SupplierProfileView account={account} profile={profile} />}
    </SupplierLayout>
  );
}
