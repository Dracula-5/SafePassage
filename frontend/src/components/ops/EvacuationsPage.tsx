"use client";

// People collected just outside a hazard zone and taken to a safe point. Everything here is the backend's own
// record; a stage or a failure is shown exactly as it was recorded.

import { useMemo, useState } from "react";

import { Evacuation } from "@/lib/api";
import { ago, plate } from "@/lib/format";

import { ActionButton, AlertCard, EmptyState, FilterChips, PageHeader, StatusBadge, Surface } from "../ui";
import styles from "./ops.module.css";
import { useOps } from "./OpsProvider";
import { EVACUATION_STAGES, EvacuateDialog } from "./parts";

type Filter = "active" | "safe" | "failed" | "all";
const ACTIVE = new Set(["waiting", "on_the_way", "on_board"]);

function Progress({ stage }: { stage: Evacuation["stage"] }) {
  const step = EVACUATION_STAGES[stage].step;
  return (
    <div className={styles.steps} role="img" aria-label={EVACUATION_STAGES[stage].text}>
      {[1, 2, 3, 4].map((n) => <span key={n} className={`${styles.step} ${stage === "failed" ? styles.stepBad : n < step ? styles.stepDone : n === step ? (stage === "safe" ? styles.stepDone : styles.stepNow) : ""}`} />)}
    </div>
  );
}

export default function EvacuationsPage() {
  const { evacuations, vehicles, hazards, refreshPlans } = useOps();
  const [filter, setFilter] = useState<Filter | null>(null);
  const [asking, setAsking] = useState(false);
  const [place, setPlace] = useState("");
  const [note, setNote] = useState<{ text: string; ok: boolean } | null>(null);

  const activeCount = evacuations.filter((e) => ACTIVE.has(e.stage)).length;
  const effective: Filter = filter ?? (activeCount > 0 ? "active" : "all");
  const shown = useMemo(() => evacuations.filter((e) => effective === "all" || (effective === "active" && ACTIVE.has(e.stage)) || (effective === "safe" && e.stage === "safe") || (effective === "failed" && e.stage === "failed")), [evacuations, effective]);
  const places = (hazards?.features ?? []).map((f) => f.properties).filter((h) => h.is_drill || h.severity === "severe" || h.severity === "extreme");
  const peopleWaiting = evacuations.filter((e) => ACTIVE.has(e.stage)).reduce((n, e) => n + e.people, 0);

  return (
    <>
      <PageHeader title="Evacuations" subtitle="People being taken from a hazard zone to safety."
        actions={<ActionButton variant="primary" icon="plus" onClick={() => { setAsking(true); setPlace(places[0]?.id ?? ""); }} disabled={places.length === 0}>Request evacuation</ActionButton>} />
      {note && <AlertCard tone={note.ok ? "good" : "bad"}>{note.text}</AlertCard>}
      {places.length === 0 && <p className={styles.muted} style={{ fontSize: 13 }}>An evacuation is requested for a drill or a severe alert, and none is active right now.</p>}

      <FilterChips label="Evacuation status" value={effective} onChange={setFilter} options={[
        { key: "active", label: "In progress", count: activeCount }, { key: "safe", label: "Reached safety", count: evacuations.filter((e) => e.stage === "safe").length },
        { key: "failed", label: "Needs a person", count: evacuations.filter((e) => e.stage === "failed").length }, { key: "all", label: "All", count: evacuations.length },
      ]} />
      {activeCount > 0 && <p className={styles.muted} style={{ fontSize: 13.5 }}>{peopleWaiting.toLocaleString()} people are waiting or on their way to safety.</p>}

      {shown.length === 0 ? (
        <Surface><EmptyState icon="evac" title="No evacuations here">{evacuations.length === 0 ? "No evacuations have been requested. In presentation mode one is requested every 10 to 15 minutes while a zone is active." : "No evacuations have this status."}</EmptyState></Surface>
      ) : (
        <div className={styles.stack}>
          {shown.map((e) => {
            const truck = vehicles.find((v) => v.id === e.vehicle_id);
            const stage = EVACUATION_STAGES[e.stage];
            return (
              <Surface key={e.id}>
                <div className={styles.detailTitle}>
                  <span className={styles.tags}>
                    <StatusBadge tone={stage.tone}>{stage.text}</StatusBadge>
                    {e.is_drill && <StatusBadge tone="drill">DRILL</StatusBadge>}
                    {e.simulated && <StatusBadge tone="warn">SIMULATED</StatusBadge>}
                  </span>
                  <small className={styles.muted}>Requested {ago(e.created_at)}</small>
                </div>
                <h2 style={{ marginTop: 8, fontSize: 17 }}>{e.people} people from {e.settlement}</h2>
                <p className={styles.muted} style={{ fontSize: 13.5 }}>{e.zone.slice(0, 120)}</p>
                <Progress stage={e.stage} />
                <dl className={styles.facts}>
                  <div className={styles.fact}><small>Pick-up point</small><b>{e.pickup_name ?? "Not chosen yet"}</b></div>
                  <div className={styles.fact}><small>Safe destination</small><b>{e.safe_name ?? "Not chosen yet"}</b></div>
                  <div className={styles.fact}><small>Truck</small><b>{truck ? <><span className="truck-no">{truck.fleet_number}</span>{truck.driver_name ?? plate(truck.label)}</> : "Not assigned"}</b></div>
                  {e.zones_avoided !== null && <div className={styles.fact}><small>Way out</small><b>Avoids {e.zones_avoided} active zone{e.zones_avoided === 1 ? "" : "s"}{e.zones_crossed ? `, crosses ${e.zones_crossed} with no way round` : ""}</b></div>}
                </dl>
                {e.failure && <AlertCard tone="bad" title="Needs a person">{e.failure}</AlertCard>}
                {truck && <a className="link" href={`/driver-view?vehicle=${truck.id}`} target="_blank" rel="noreferrer">Open the driver&apos;s screen</a>}
              </Surface>
            );
          })}
        </div>
      )}

      <EvacuateDialog hazardId={asking ? place || null : null} label={places.find((h) => h.id === place)?.headline.slice(0, 60)} onClose={() => setAsking(false)}
        choices={places.map((h) => ({ id: h.id, label: `${h.is_drill ? "DRILL · " : ""}${h.headline.slice(0, 70)}` }))} onChoose={setPlace}
        onResult={(text, ok) => { setNote({ text, ok }); refreshPlans(); }} />
    </>
  );
}
