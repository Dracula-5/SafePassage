"use client";

// The frame around a signed-in screen: collapsible sidebar and compact top bar on wide screens, a drawer
// menu on phones, and a bottom bar for the apps that live on a phone. Pages only supply content.

import Link from "next/link";
import { ReactNode, useEffect, useState } from "react";

import Icon, { BrandMark } from "./icons";
import styles from "./ui.module.css";

export interface NavItem {
  key: string;
  label: string;
  icon: string;
  /** A link target, or none for an item that runs `onSelect`. */
  href?: string;
  onSelect?: () => void;
  active?: boolean;
  /** A count worth a person's attention, such as plans awaiting a decision. Zero shows nothing. */
  badge?: number;
}

function NavEntry({ item, className, activeClass, children }: { item: NavItem; className: string; activeClass: string; children: ReactNode }) {
  const cls = `${className} ${item.active ? activeClass : ""}`;
  return item.href
    ? <Link href={item.href} className={cls} aria-current={item.active ? "page" : undefined} title={item.label}>{children}</Link>
    : <button type="button" className={cls} aria-current={item.active ? "page" : undefined} onClick={item.onSelect} title={item.label}>{children}</button>;
}

/** The bar along the bottom of a phone screen. At least 44px targets, safe-area aware. */
export function MobileNavigation({ items, label = "Sections" }: { items: NavItem[]; label?: string }) {
  return (
    <nav className={styles.bottomNav} aria-label={label}>
      {items.map((item) => (
        <NavEntry key={item.key} item={item} className={styles.bottomItem} activeClass={styles.bottomActive}>
          <Icon name={item.icon} size={24} />
          <span>{item.label}</span>
          {item.badge ? <b className={styles.bottomBadge}>{item.badge}</b> : null}
        </NavEntry>
      ))}
    </nav>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <header className={styles.pageHeader}>
      <div>
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {actions && <div className={styles.pageActions}>{actions}</div>}
    </header>
  );
}

const COLLAPSED_KEY = "sp_nav_collapsed";

export function AppLayout({ items, title, status, accountName, accountDetail, onSignOut, brand = "SafePassage", wide = false, children }: {
  items: NavItem[];
  title: string;
  status?: ReactNode;
  accountName: string;
  accountDetail: string;
  onSignOut: () => void;
  brand?: string;
  /** Give the page the whole workspace, with no padding: for a map. */
  wide?: boolean;
  children: ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(COLLAPSED_KEY) === "1";
    } catch {
      return false;
    }
  });
  const [open, setOpen] = useState(false);
  const activeKey = items.find((i) => i.active)?.key;

  // Leaving for another page closes the phone menu.
  useEffect(() => {
    const close = () => setOpen(false);
    const timer = setTimeout(close, 0);
    return () => clearTimeout(timer);
  }, [activeKey]);

  function toggle() {
    setCollapsed((was) => {
      try {
        localStorage.setItem(COLLAPSED_KEY, was ? "0" : "1");
      } catch {
        // The choice just won't be remembered.
      }
      return !was;
    });
  }

  return (
    <div className={`${styles.frame} ${collapsed ? styles.frameCollapsed : ""}`}>
      {open && <button type="button" className={styles.scrim} aria-label="Close menu" onClick={() => setOpen(false)} />}
      <aside className={`${styles.sidebar} ${open ? styles.sidebarOpen : ""}`} aria-label="Main menu">
        <Link href="/" className={styles.brand} title={brand}><BrandMark size={30} /><span className={styles.brandText}>{brand}</span></Link>
        <nav className={styles.navList} aria-label="Pages">
          {items.map((item) => (
            <NavEntry key={item.key} item={item} className={styles.navItem} activeClass={styles.navActive}>
              <Icon name={item.icon} size={22} />
              <span className={styles.navLabel}>{item.label}</span>
              {item.badge ? <b className={styles.navBadge}>{item.badge}</b> : null}
            </NavEntry>
          ))}
        </nav>
        <div className={styles.sideFoot}>
          <div className={styles.who}>
            <span className={styles.avatar} aria-hidden="true">{accountName.charAt(0).toUpperCase()}</span>
            <span className={styles.whoText}><b>{accountName}</b><small>{accountDetail}</small></span>
          </div>
          <button type="button" className={styles.navItem} onClick={onSignOut} title="Sign out"><Icon name="logout" size={22} /><span className={styles.navLabel}>Sign out</span></button>
          <button type="button" className={`${styles.navItem} ${styles.onlyWide}`} onClick={toggle} aria-pressed={collapsed} title={collapsed ? "Expand menu" : "Collapse menu"}>
            <Icon name="panel" size={22} /><span className={styles.navLabel}>{collapsed ? "Expand" : "Collapse"}</span>
          </button>
        </div>
      </aside>

      <div className={styles.main}>
        <header className={styles.topbar}>
          <button type="button" className={`${styles.iconBtn} ${styles.onlyNarrow}`} aria-label="Open menu" aria-expanded={open} onClick={() => setOpen(true)}><Icon name="menu" /></button>
          <h2 className={`${styles.topTitle} ${wide ? "" : styles.titleNarrow}`}>{title}</h2>
          <span className="spacer" />
          {status}
        </header>
        <main id="main" className={wide ? styles.contentWide : styles.content}>{children}</main>
      </div>
    </div>
  );
}
