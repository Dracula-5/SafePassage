"use client";

// A full-screen workspace for what-if scenarios. A scenario is a drill: a hazard area the duty officer draws, always
// labelled DRILL. The planning agents react to it like a real alert and make a plan, which waits for approval on
// Plans & Approvals. This page never dispatches anything itself.

import dynamic from "next/dynamic";
import Link from "next/link";
import { useMemo, useState } from "react";

import { api, PlanRoute, Severity } from "@/lib/api";
import { ago } from "@/lib/format";

import { ActionButton, AlertCard, StatusBadge } from "../ui";
import { HAZARD_TYPES, SEVERITIES, words } from "./constants";
import styles from "./ops.module.css";
import { useOps } from "./OpsProvider";
import { PlanStatus } from "./parts";

const LiveMap = dynamic(() => import("../LiveMap"), { ssr: false, loading: () => <div className="map map-loading">Loading map…</div> });
const NO_ROUTES: PlanRoute[] = [];
const DURATIONS = [30, 60, 120, 240];

export default function SimulationPage() {
  const { hazards, vehicles, liveRoutes, plans, bounds, reloadHazards, refreshPlans } = useOps();
  const [drawing, setDrawing] = useState(false);
  const [vertices, setVertices] = useState<[number, number][]>([]);
  const [form, setForm] = useState({ label: "", hazard_type: "flood", severity: "severe" as Severity, ttl_minutes: 120 });
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; ok: boolean } | null>(null);

  const drills = useMemo(() => (hazards?.features ?? []).map((f) => f.properties).filter((h) => h.is_drill), [hazards]);
  const ready = vertices.length >= 3 && form.label.trim().length > 0;

  async function publish() {
    if (!ready) return;
    setBusy(true);
    try {
      await api.createDrill({ ...form, label: form.label.trim(), geometry: { type: "Polygon", coordinates: [[...vertices, vertices[0]]] } });
      setNote({ text: "Scenario published. The planning agents are making a plan; it appears below within a minute.", ok: true });
      setDrawing(false);
      setVertices([]);
      setForm({ ...form, label: "" });
      reloadHazards();
      refreshPlans();
    } catch (err) {
      setNote({ text: (err as Error).message, ok: false });
    } finally {
      setBusy(false);
    }
  }

  async function endDrill(id: string) {
    try {
      await api.cancelDrill(id);
      reloadHazards();
    } catch (err) {
      setNote({ text: (err as Error).message, ok: false });
    }
  }

  return (
    <div className={`${styles.stage} ${styles.stageFull}`}>
      <LiveMap hazards={hazards} initialBounds={bounds} drawing={drawing} draftVertices={vertices} onMapClick={(c) => setVertices((v) => [...v, c])}
        onCancelDrill={endDrill} onFacilitiesStatus={() => undefined} onFindDrivers={() => undefined} onAskTruck={() => undefined} onEvacuate={() => undefined}
        vehicles={vehicles} routes={NO_ROUTES} liveRoutes={liveRoutes} />

      <div className={`${styles.float} ${styles.simBanner}`}>SIMULATION · drills are labelled DRILL everywhere</div>

      <section className={`${styles.float} ${styles.scenario}`} aria-label="Scenario controls">
        <h2>What-if scenario</h2>
        {!drawing ? (
          <>
            <p className={styles.muted} style={{ fontSize: 13.5 }}>Draw a hazard area to see how the system would plan around it. Nothing is dispatched until you approve a plan.</p>
            <ActionButton variant="primary" icon="plus" onClick={() => { setDrawing(true); setVertices([]); setNote(null); }}>Draw a scenario</ActionButton>
          </>
        ) : (
          <div className={styles.scenarioForm}>
            <p className={styles.muted} style={{ fontSize: 13.5 }}>Click the map to add corners. {vertices.length} so far, 3 needed.</p>
            <label>Name<input placeholder="e.g. Krishna embankment breach" value={form.label} maxLength={200} onChange={(e) => setForm({ ...form, label: e.target.value })} /></label>
            <div className="row">
              <label>Type<select value={form.hazard_type} onChange={(e) => setForm({ ...form, hazard_type: e.target.value })}>{HAZARD_TYPES.map((t) => <option key={t} value={t}>{words(t)}</option>)}</select></label>
              <label>Severity<select value={form.severity} onChange={(e) => setForm({ ...form, severity: e.target.value as Severity })}>{SEVERITIES.map((s) => <option key={s} value={s}>{words(s)}</option>)}</select></label>
            </div>
            <label>Lasts<select value={form.ttl_minutes} onChange={(e) => setForm({ ...form, ttl_minutes: Number(e.target.value) })}>{DURATIONS.map((m) => <option key={m} value={m}>{m >= 60 ? `${m / 60} hour${m > 60 ? "s" : ""}` : `${m} minutes`}</option>)}</select></label>
            <div className="row">
              <ActionButton variant="primary" busy={busy} disabled={!ready} onClick={publish}>Publish scenario</ActionButton>
              <ActionButton onClick={() => setVertices((v) => v.slice(0, -1))} disabled={vertices.length === 0}>Undo</ActionButton>
              <ActionButton onClick={() => { setDrawing(false); setVertices([]); }}>Cancel</ActionButton>
            </div>
          </div>
        )}
        {note && <AlertCard tone={note.ok ? "good" : "bad"}>{note.text}</AlertCard>}
      </section>

      <section className={`${styles.float} ${styles.results}`} aria-label="Scenario results">
        <h2 style={{ fontSize: 16 }}>Scenarios ({drills.length})</h2>
        {drills.length === 0 && <p className={styles.muted} style={{ fontSize: 13.5 }}>No scenario is running. Draw one to see the plan it produces.</p>}
        {drills.map((d) => {
          const made = plans.filter((p) => p.hazard_ids.includes(d.id));
          return (
            <article key={d.id} className="card" style={{ padding: 14, gap: 8 }}>
              <div className="row-between"><StatusBadge tone="drill">DRILL</StatusBadge><small className={styles.muted}>{ago(d.fetched_at)}</small></div>
              <b style={{ fontSize: 14 }}>{d.headline}</b>
              <small className={styles.muted}>{words(d.hazard_type)} · {d.severity}{d.expires_at ? ` · ends ${new Date(d.expires_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""}</small>
              {made.length === 0 ? <small className={styles.muted}>No plan yet. The agents are working on it, or no populated area is affected.</small> : made.slice(0, 3).map((p) => (
                <div key={p.id} className={styles.stackTight}>
                  <span className={styles.tags}><PlanStatus status={p.status} /><Link href={`/dashboard/plans?plan=${p.id}`} className="small">Review plan</Link></span>
                  {p.summary && <small className={styles.muted}>{p.summary.slice(0, 140)}</small>}
                </div>
              ))}
              <button type="button" className="danger small" onClick={() => endDrill(d.id)}>End scenario</button>
            </article>
          );
        })}
      </section>
    </div>
  );
}
