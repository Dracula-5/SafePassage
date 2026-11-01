// Small pieces every supplier screen shares.

import { ReactNode } from "react";

import { useNow } from "../LiveStatus";
import Icon from "./icons";
import { Tone } from "./orders";
import styles from "./supplier.module.css";
import { back, View } from "./nav";

const TONE_CLASS: Record<Tone, string> = { good: styles.toneGood, warn: styles.toneWarn, info: styles.toneInfo, plain: styles.tonePlain, bad: styles.toneBad };

export function StatusBadge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`${styles.badge} ${TONE_CLASS[tone]}`}>{children}</span>;
}

export function DrillBadge() {
  return <span className={`${styles.badge} ${styles.drill}`}>DRILL</span>;
}

/** The real verification state, from SupplierProfile.verified. */
export function VerificationBadge({ verified }: { verified: boolean }) {
  return (
    <StatusBadge tone={verified ? "good" : "warn"}>
      <Icon name={verified ? "shield" : "clock"} size={14} />
      {verified ? "Verified" : "Awaiting Verification"}
    </StatusBadge>
  );
}

export const UNVERIFIED_NOTE = "A duty officer checks any plan that uses your stock until your organisation is verified.";

export function Notice({ tone, children }: { tone: "warn" | "good" | "bad"; children: ReactNode }) {
  const cls = tone === "warn" ? styles.noticeWarn : tone === "good" ? styles.noticeGood : styles.noticeBad;
  return (
    <div className={`${styles.notice} ${cls}`} role={tone === "bad" ? "alert" : "status"}>
      <Icon name={tone === "good" ? "check" : "alert"} size={18} />
      <span>{children}</span>
    </div>
  );
}

export function EmptyState({ icon, title, children, action }: { icon: string; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className={styles.empty}>
      <span className={styles.emptyArt}><Icon name={icon} size={40} /></span>
      <h2>{title}</h2>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function LoadingSkeleton() {
  return (
    <div className={styles.content} role="status" aria-label="Loading your supplier portal">
      <div className={styles.skeleton} style={{ height: 56, width: "60%" }} />
      <div className={styles.skeleton} style={{ height: 150 }} />
      <div className={styles.summaryGrid}>
        {[0, 1, 2, 3].map((i) => <div key={i} className={styles.skeleton} style={{ height: 76 }} />)}
      </div>
    </div>
  );
}

export function SummaryCard({ icon, tone, label, value, small, onClick }: {
  icon: string;
  tone?: "violet" | "amber" | "good" | "cyan";
  label: string;
  value: string;
  small?: boolean;
  onClick: () => void;
}) {
  const bubble = { violet: styles.bubbleViolet, amber: styles.bubbleAmber, good: styles.bubbleGood, cyan: styles.bubbleCyan }[tone ?? "cyan"];
  return (
    <button type="button" className={styles.summary} onClick={onClick}>
      <span className={`${styles.bubble} ${bubble}`}><Icon name={icon} /></span>
      <span className={styles.summaryText}>
        <span className={styles.summaryLabel}>{label}</span>
        <span className={`${styles.summaryValue} ${small ? styles.summaryValueSmall : ""}`}>{value}</span>
      </span>
    </button>
  );
}

export function PageHeader({ title, backTo, action, backMobileOnly }: { title: string; backTo?: View; action?: ReactNode; backMobileOnly?: boolean }) {
  return (
    <div className={styles.pageHead}>
      {backTo ? (
        <button type="button" className={`${styles.backBtn} ${backMobileOnly ? styles.backMobileOnly : ""}`} onClick={() => back(backTo)} aria-label="Go back">
          <Icon name="back" />
        </button>
      ) : <span className={styles.pageHeadSpacer} />}
      <h1>{title}</h1>
      {action ? <span className={styles.pageHeadAction}>{action}</span> : <span className={styles.pageHeadSpacer} />}
    </div>
  );
}

/** "Live" with seconds since the last real answer from the server. Same rules as the other screens' indicator. */
export function LivePill({ updatedAt, failed }: { updatedAt: number | null; failed: boolean }) {
  const now = useNow();
  const seconds = updatedAt && now ? Math.max(0, Math.round((now - updatedAt) / 1000)) : null;
  const late = seconds !== null && seconds > 15;
  const state = failed || late ? "down" : seconds === null ? "wait" : "on";
  const cls = state === "down" ? styles.liveDown : state === "wait" ? styles.liveWait : "";
  return (
    <span className={`${styles.livePill} ${cls}`} role="status">
      <span className={styles.liveDot} />
      {state === "down" ? "Connection lost" : state === "wait" ? "Connecting" : "Live"}
      {seconds !== null && <small>updated {seconds}s ago</small>}
    </span>
  );
}
