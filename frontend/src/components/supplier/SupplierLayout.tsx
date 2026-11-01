"use client";

// The frame: a sidebar on desktop, a header and a bottom bar on phones and tablets.

import { ReactNode, useEffect } from "react";

import { Account } from "@/lib/api";
import { signOut } from "@/lib/session";

import Icon, { BrandMark } from "./icons";
import { go, View } from "./nav";
import styles from "./supplier.module.css";
import { LivePill } from "./ui";

const TABS = [
  { name: "home", label: "Home", icon: "home" },
  { name: "orders", label: "Orders", icon: "orders" },
  { name: "inventory", label: "Inventory", icon: "boxes" },
  { name: "profile", label: "Profile", icon: "user" },
] as const;

/** Which tab a screen belongs to: an order opens from Orders, and Report Stock sits under Inventory. */
function tabOf(view: View): string {
  return view.name === "order" ? "orders" : view.name === "report" ? "inventory" : view.name;
}

export default function SupplierLayout({ account, view, updatedAt, failed, children }: {
  account: Account;
  view: View;
  updatedAt: number | null;
  failed: boolean;
  children: ReactNode;
}) {
  const active = tabOf(view);
  const key = view.name === "order" ? `order/${view.id}` : view.name;
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [key]);

  return (
    <div className={styles.root}>
      <aside className={styles.sidebar} aria-label="Supplier menu">
        <span className={styles.brand}><BrandMark /> SafePassage</span>
        <nav className={styles.sideNav} aria-label="Supplier sections">
          {TABS.map((t) => (
            <button key={t.name} type="button" className={`${styles.sideItem} ${active === t.name ? styles.sideActive : ""}`}
              aria-current={active === t.name ? "page" : undefined} onClick={() => go({ name: t.name })}>
              <Icon name={t.icon} /> {t.label}
            </button>
          ))}
        </nav>
        <div className={styles.sideCta}>
          <button type="button" className={styles.primaryBtn} onClick={() => go({ name: "report" })}><Icon name="plus" size={18} /> Report Stock</button>
        </div>
        <div className={styles.sideFoot}>
          <div className={styles.sideAccount}>
            <span className={`${styles.bubble} ${styles.bubbleCyan}`}><Icon name="user" /></span>
            <div><b>{account.name}</b><small>{account.email}</small></div>
          </div>
          <button type="button" className={styles.ghostBtn} onClick={signOut}><Icon name="logout" size={18} /> Sign out</button>
        </div>
      </aside>

      <div className={styles.main}>
        <header className={styles.header}>
          <span className={styles.brand}><BrandMark /> SafePassage</span>
          <LivePill updatedAt={updatedAt} failed={failed} />
        </header>
        <main className={styles.content}>{children}</main>
      </div>

      <nav className={styles.bottomNav} aria-label="Supplier sections">
        {TABS.map((t) => (
          <button key={t.name} type="button" className={`${styles.bottomItem} ${active === t.name ? styles.bottomActive : ""}`}
            aria-current={active === t.name ? "page" : undefined} onClick={() => go({ name: t.name })}>
            <Icon name={t.icon} size={24} />
            <span>{t.label}</span>
          </button>
        ))}
      </nav>
    </div>
  );
}
