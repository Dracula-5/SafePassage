"use client";

// What the system has done, and why, as it happens. The stream delivers events from the moment this page opens
// (and resumes by itself after a dropped connection); older steps for a plan are on its Plans & Approvals page.

import Link from "next/link";
import { useMemo, useState } from "react";

import { AgentEvent } from "@/lib/api";

import { AlertCard, EmptyState, FilterChips, Icon, PageHeader, StatusBadge } from "../ui";
import styles from "./ops.module.css";
import { useOps } from "./OpsProvider";

type Group = "all" | "decisions" | "validation" | "approvals" | "dispatch" | "problems";

const GROUPS: { key: Group; label: string; has: (e: { node: string; event_type: string }) => boolean }[] = [
  { key: "all", label: "All", has: () => true },
  { key: "decisions", label: "Decisions", has: (e) => ["decision", "node_finished"].includes(e.event_type) },
  // Everything the Critic did, passes included: a count of only the rejections would read as "nothing was checked".
  { key: "validation", label: "Safety checks", has: (e) => e.node === "critic" || e.event_type === "critic_rejection" },
  { key: "approvals", label: "Approvals", has: (e) => ["approval_requested", "approval_decided"].includes(e.event_type) },
  { key: "dispatch", label: "Dispatch & routes", has: (e) => e.event_type === "dispatch" },
  { key: "problems", label: "Problems", has: (e) => ["error", "data_stale"].includes(e.event_type) },
];

function tone(type: string): string {
  if (type === "error" || type === "critic_rejection") return styles.eventError;
  if (type === "data_stale" || type === "approval_requested") return styles.eventWarn;
  if (type === "decision" || type === "dispatch" || type === "approval_decided") return styles.eventGood;
  return styles.eventPlain;
}

function Entry({ e }: { e: AgentEvent }) {
  const [open, setOpen] = useState(false);
  const hasMore = Object.keys(e.payload ?? {}).length > 0 || e.source_ids.length > 0;
  return (
    <li className={`${styles.event} ${tone(e.event_type)}`}>
      <div className={styles.eventHead}>
        <span className={styles.tags}><b>{e.node.replace(/_/g, " ")}</b><StatusBadge tone="plain">{e.event_type.replace(/_/g, " ")}</StatusBadge></span>
        <small className={styles.muted}>{new Date(e.created_at).toLocaleTimeString()}</small>
      </div>
      <p className={styles.eventBody}>{e.message}</p>
      <div className="row" style={{ marginTop: 6 }}>
        {e.plan_id && <Link href={`/dashboard/plans?plan=${e.plan_id}`} className="small">Open the plan</Link>}
        {hasMore && <button type="button" className="link" aria-expanded={open} onClick={() => setOpen((o) => !o)}>{open ? "Hide detail" : "Show detail"}</button>}
      </div>
      {open && <pre className={styles.payload}>{JSON.stringify({ payload: e.payload, source_ids: e.source_ids }, null, 2)}</pre>}
    </li>
  );
}

export default function ActivityPage() {
  const { events, stream } = useOps();
  const [group, setGroup] = useState<Group>("all");
  const has = GROUPS.find((g) => g.key === group)?.has ?? GROUPS[0].has;
  const shown = useMemo(() => events.filter(has), [events, has]);

  return (
    <>
      <PageHeader title="Activity & AI Decisions" subtitle="Every step the planning agents take, with the reason, as it happens." />
      {stream === "reconnecting" && <AlertCard tone="warn" title="Live updates interrupted">New steps will appear once the connection returns.</AlertCard>}
      <FilterChips label="Event type" value={group} onChange={setGroup}
        options={GROUPS.map((g) => ({ key: g.key, label: g.label, count: events.filter(g.has).length }))} />
      {shown.length === 0 ? (
        <EmptyState icon="activity" title="Nothing yet">
          {events.length === 0 ? "Steps appear here the moment they happen. This list starts from when you opened the page." : "No events of this type yet."}
        </EmptyState>
      ) : (
        <ol className={styles.timeline} aria-label="Events, newest first">{shown.map((e) => <Entry key={e.id} e={e} />)}</ol>
      )}
      <p className={styles.muted} style={{ fontSize: 13 }}><Icon name="clock" size={14} /> Showing the latest {Math.min(events.length, 300)} events since this page opened.</p>
    </>
  );
}
