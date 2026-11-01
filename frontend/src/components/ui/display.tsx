"use client";

// Things that show a state: metrics, badges, alerts, empty and loading screens, and the connection indicator.

import Link from "next/link";
import { ReactNode } from "react";

import { useNow } from "../LiveStatus";
import Icon from "./icons";
import styles from "./ui.module.css";

export type Tone = "good" | "warn" | "bad" | "info" | "plain" | "drill";

const TONE: Record<Tone, string> = { good: styles.tGood, warn: styles.tWarn, bad: styles.tBad, info: styles.tInfo, plain: styles.tPlain, drill: styles.tDrill };

export function StatusBadge({ tone = "plain", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`${styles.badge} ${TONE[tone]}`}>{children}</span>;
}

/** One number that matters. With `href` it opens the page that explains it. */
export function MetricCard({ label, value, note, tone, href }: { label: string; value: string | number; note?: string; tone?: "good" | "warn" | "bad"; href?: string }) {
  const cls = `${styles.metric} ${tone ? { good: styles.toneGood, warn: styles.toneWarn, bad: styles.toneBad }[tone] : ""}`;
  const body = (
    <>
      <span className={styles.metricLabel}>{label}</span>
      <b className={styles.metricValue}>{value}</b>
      {note && <span className={styles.metricNote}>{note}</span>}
    </>
  );
  return href ? <Link href={href} className={cls}>{body}</Link> : <div className={cls}>{body}</div>;
}

export function MetricRow({ children }: { children: ReactNode }) {
  return <section className={styles.metrics} aria-label="Summary">{children}</section>;
}

export function AlertCard({ tone = "info", title, children, action }: { tone?: "warn" | "bad" | "good" | "info"; title?: string; children?: ReactNode; action?: ReactNode }) {
  const cls = { warn: styles.aWarn, bad: styles.aBad, good: styles.aGood, info: styles.aInfo }[tone];
  return (
    <div className={`${styles.alertCard} ${cls}`} role={tone === "bad" ? "alert" : "status"}>
      <Icon name={tone === "good" ? "check" : tone === "info" ? "alert" : "alert"} size={20} />
      <div className={styles.alertBody}>{title && <b>{title}</b>}{children && <span>{children}</span>}</div>
      {action}
    </div>
  );
}

export function Surface({ title, action, flush, children }: { title?: string; action?: ReactNode; flush?: boolean; children: ReactNode }) {
  return (
    <section className={`${styles.surface} ${flush ? styles.surfaceFlush : ""}`}>
      {(title || action) && <div className={styles.surfaceHead} style={flush ? { padding: "18px 20px 0" } : undefined}><h2>{title}</h2>{action}</div>}
      {children}
    </section>
  );
}

export function EmptyState({ icon = "box", title, children, action }: { icon?: string; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className={styles.emptyState}>
      <span className={styles.emptyArt}><Icon name={icon} size={38} /></span>
      <h2>{title}</h2>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function LoadingSkeleton({ rows = 3, height = 72 }: { rows?: number; height?: number }) {
  return (
    <div role="status" aria-label="Loading" style={{ display: "grid", gap: 12 }}>
      {Array.from({ length: rows }, (_, i) => <div key={i} className={styles.skeleton} style={{ height }} />)}
    </div>
  );
}

/** "Live", "Connecting" or "Connection lost", from when the last real answer arrived. Never hides a failure. */
export function ConnectionStatus({ updatedAt, failed, degraded }: { updatedAt: number | null; failed: boolean; degraded?: boolean }) {
  const now = useNow();
  const seconds = updatedAt && now ? Math.max(0, Math.round((now - updatedAt) / 1000)) : null;
  const late = seconds !== null && seconds > 15;
  const state = failed || late ? "down" : seconds === null ? "wait" : degraded ? "wait" : "on";
  const cls = state === "down" ? styles.pillBad : state === "wait" ? styles.pillWait : "";
  const text = state === "down" ? "Connection lost" : state === "wait" ? (degraded ? "Live updates paused" : "Connecting") : "Live";
  return (
    <span className={`${styles.statusPill} ${cls}`} role="status" data-testid="connection-status">
      <i />{text}{seconds !== null && <small>updated {seconds}s ago</small>}
    </span>
  );
}
