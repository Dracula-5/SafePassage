"use client";

// Where the data comes from, and whether each source is reporting on time. Status words come from the source's
// own health record: stale stays stale, a backup stays labelled as a backup, a paused source says it is paused.

import { SourceHealth } from "@/lib/api";
import { ago, sourceName } from "@/lib/format";

import { AlertCard, EmptyState, PageHeader, StatusBadge, Tone } from "../ui";
import styles from "./ops.module.css";
import { useOps } from "./OpsProvider";

const WORKERS = new Set(["brain_worker", "ingestion_worker"]);

function describe(s: SourceHealth): { text: string; tone: Tone; note: string } {
  if (s.breaker_state !== "closed") return { text: "Paused", tone: "bad", note: "Paused after repeated failures; it is tried again on its own." };
  if (s.is_stale) return { text: "Stale", tone: "bad", note: "It has not reported within its expected time." };
  if (s.fallback_source) return { text: "Using backup", tone: "warn", note: `Backup source: ${s.fallback_source}.` };
  return { text: "Operational", tone: "good", note: "Reporting on time." };
}

function every(seconds: number): string {
  if (seconds < 120) return `${seconds} seconds`;
  if (seconds < 7200) return `${Math.round(seconds / 60)} minutes`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)} hours`;
  return `${Math.round(seconds / 86400)} days`;
}

function SourceCard({ s }: { s: SourceHealth }) {
  const state = describe(s);
  return (
    <article className={styles.source}>
      <div className={styles.sourceTop}>
        <h3>{sourceName(s.source)}</h3>
        <StatusBadge tone={state.tone}>{state.text}</StatusBadge>
      </div>
      <p className={styles.muted} style={{ fontSize: 13.5 }}>{state.note}</p>
      <dl className={styles.facts} style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div className={styles.fact}><small>Last success</small><b>{ago(s.last_success_at)}</b></div>
        <div className={styles.fact}><small>Expected within</small><b>{every(s.ttl_seconds)}</b></div>
        <div className={styles.fact}><small>Last failure</small><b>{s.last_failure_at ? ago(s.last_failure_at) : "None recorded"}</b></div>
        <div className={styles.fact}><small>Fallback</small><b>{s.fallback_source ?? "None in use"}</b></div>
      </dl>
      {s.last_error && (
        <details>
          <summary style={{ cursor: "pointer", fontSize: 13.5, fontWeight: 600 }}>Last error</summary>
          <p className={styles.payload}>{s.last_error}</p>
        </details>
      )}
    </article>
  );
}

export default function SourcesPage() {
  const { health, failed } = useOps();
  const sources = health.filter((s) => !WORKERS.has(s.source));
  const workers = health.filter((s) => WORKERS.has(s.source));
  const trouble = health.filter((s) => s.is_stale || s.breaker_state !== "closed").length;
  const onBackup = health.filter((s) => s.fallback_source && !s.is_stale && s.breaker_state === "closed").length;

  return (
    <>
      <PageHeader title="Data Sources" subtitle="Each source publishes at its own pace: alerts about every 2 minutes, forecasts every few hours." />
      {failed && <AlertCard tone="bad" title="Cannot reach the server">The statuses below may be out of date.</AlertCard>}
      {health.length > 0 && (trouble > 0
        ? <AlertCard tone="warn" title={`${trouble} of ${health.length} need a look`}>Stale or paused sources are marked below. Plans use the data they have and say so.</AlertCard>
        : onBackup > 0
          ? <AlertCard tone="warn" title={`${onBackup} source${onBackup === 1 ? " is" : "s are"} using backup data`}>Nothing is stale, but the backup may be older than the live feed. Each one is marked below.</AlertCard>
          : <AlertCard tone="good" title="Every source is reporting on time" />)}
      {health.length === 0 && <EmptyState icon="database" title="No source status yet">Source health appears here once the data workers have reported.</EmptyState>}
      {sources.length > 0 && <div className={styles.sourceGrid}>{sources.map((s) => <SourceCard key={s.source} s={s} />)}</div>}
      {workers.length > 0 && (
        <>
          <h2>Workers</h2>
          <div className={styles.sourceGrid}>{workers.map((s) => <SourceCard key={s.source} s={s} />)}</div>
        </>
      )}
    </>
  );
}
