"use client";

// Home of the duty officer: four numbers that matter, and the live map. Anything that needs a decision is
// listed in the small "Needs attention" panel; everything else lives on its own page.

import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { api, PlanRoute } from "@/lib/api";
import { sourceName, tonnes } from "@/lib/format";

import { AlertCard, Icon, MetricCard, MetricRow, PageHeader } from "../ui";
import { EVACUATION_ROUTE, SEVERITIES, SEVERITY_COLORS, SIMULATED_TRUCK, SUPPLY_ROUTE } from "./constants";
import styles from "./ops.module.css";
import { useOps } from "./OpsProvider";
import { EvacuateDialog, planLabel } from "./parts";

const LiveMap = dynamic(() => import("../LiveMap"), { ssr: false, loading: () => <div className="map map-loading">Loading map…</div> });
const NO_ROUTES: PlanRoute[] = [];

export default function CommandCenter() {
  const router = useRouter();
  const { status, overview, hazards, vehicles, liveRoutes, plans, evacuations, health, bounds, reloadHazards } = useOps();
  const [open, setOpen] = useState(true);
  const [facilities, setFacilities] = useState("");
  const [evacuateFor, setEvacuateFor] = useState<string | null>(null);
  const [note, setNote] = useState<{ text: string; ok: boolean } | null>(null);

  const awaiting = plans.filter((p) => p.status === "awaiting_approval");
  const problemSources = health.filter((s) => s.is_stale || s.breaker_state !== "closed");
  const evacuationsStuck = evacuations.filter((e) => e.stage === "failed");
  const offlineOnRoute = vehicles.filter((v) => v.on_route && v.is_offline);
  const items = awaiting.length + problemSources.length + evacuationsStuck.length + offlineOnRoute.length;
  const simulated = vehicles.filter((v) => v.simulated).length;

  async function endDrill(id: string) {
    try {
      await api.cancelDrill(id);
      reloadHazards();
    } catch (err) {
      setNote({ text: (err as Error).message, ok: false });
    }
  }

  return (
    <>
      <PageHeader title="Command Center" subtitle="What is happening across the region right now, and what needs your decision." />
      <MetricRow>
        <MetricCard label="Active hazards" value={status?.hazards_active ?? "–"} note={`${status?.hazards_severe ?? 0} severe or worse`} href="/dashboard/hazards"
          tone={status?.hazards_severe ? "warn" : undefined} />
        <MetricCard label="Plans awaiting approval" value={overview?.plans_awaiting ?? "–"} note="need a decision" href="/dashboard/plans"
          tone={overview?.plans_awaiting ? "bad" : undefined} />
        <MetricCard label="Trucks online" value={overview ? `${overview.trucks_online}/${overview.trucks_total}` : "–"} note={simulated ? `${simulated} simulated` : "reporting GPS"} href="/dashboard/fleet" />
        <MetricCard label="Active deliveries" value={overview?.routes_live ?? "–"} note={overview ? `${tonnes(overview.kg_on_routes)} on the road` : undefined} href="/dashboard/plans"
          tone={overview?.orders_delivered ? "good" : undefined} />
      </MetricRow>

      {note && <AlertCard tone={note.ok ? "good" : "bad"}>{note.text}</AlertCard>}

      <div className={styles.stage}>
        <LiveMap
          hazards={hazards}
          initialBounds={bounds}
          drawing={false}
          draftVertices={[]}
          onMapClick={() => undefined}
          onCancelDrill={endDrill}
          onFacilitiesStatus={setFacilities}
          onFindDrivers={(id) => router.push(`/dashboard/fleet?place=${id}`)}
          onAskTruck={(id) => router.push(`/dashboard/fleet?truck=${id}`)}
          onEvacuate={setEvacuateFor}
          vehicles={vehicles}
          routes={NO_ROUTES}
          liveRoutes={liveRoutes}
        />

        <section className={`${styles.float} ${styles.attention}`} aria-label="Needs attention">
          <div className={styles.attentionHead}>
            <b>Needs attention{items > 0 ? ` (${items})` : ""}</b>
            <button type="button" className="small" aria-expanded={open} onClick={() => setOpen((o) => !o)}>{open ? "Hide" : "Show"}</button>
          </div>
          {open && (
            <div className={styles.attentionList}>
              {awaiting.slice(0, 4).map((p) => (
                <Link key={p.id} href={`/dashboard/plans?plan=${p.id}`} className={styles.attentionItem}>
                  <b>{planLabel(p.status).text}{p.is_drill ? " · DRILL" : ""}{p.evacuation ? " · Evacuation" : ""}</b>
                  <span className={styles.muted}>{(p.summary ?? "A plan needs your decision.").slice(0, 90)}</span>
                </Link>
              ))}
              {awaiting.length > 4 && <Link href="/dashboard/plans" className={styles.link}>See all {awaiting.length} plans</Link>}
              {problemSources.map((s) => (
                <Link key={s.source} href="/dashboard/sources" className={styles.attentionItem}>
                  <b>{sourceName(s.source)}</b>
                  <span className={styles.muted}>{s.is_stale ? "Data is stale" : "Paused after failures"}{s.fallback_source ? `, using ${s.fallback_source}` : ""}</span>
                </Link>
              ))}
              {evacuationsStuck.map((e) => (
                <Link key={e.id} href="/dashboard/evacuations" className={styles.attentionItem}>
                  <b>Evacuation needs a person</b>
                  <span className={styles.muted}>{e.people} people from {e.settlement}</span>
                </Link>
              ))}
              {offlineOnRoute.map((v) => (
                <Link key={v.id} href="/dashboard/fleet" className={styles.attentionItem}>
                  <b>Truck {v.fleet_number} lost GPS on a route</b>
                  <span className={styles.muted}>Its goods move to another truck if it stays silent.</span>
                </Link>
              ))}
              {items === 0 && <p className={styles.muted} style={{ fontSize: 13.5 }}>Nothing needs your attention right now.</p>}
            </div>
          )}
        </section>

        <div className={`${styles.float} ${styles.legend}`} aria-hidden="true">
          {SEVERITIES.map((s) => <span key={s}><i style={{ background: SEVERITY_COLORS[s] }} />{s}</span>)}
          <span><i className="line" style={{ background: SUPPLY_ROUTE }} />supply route</span>
          <span><i className="line" style={{ background: EVACUATION_ROUTE }} />evacuation</span>
          <span><i className="round" style={{ background: "#fff", boxShadow: `0 0 0 2px ${SIMULATED_TRUCK}` }} />{simulated > 0 ? "simulated truck" : "truck"}</span>
        </div>
        {facilities && <div className={`${styles.float} ${styles.mapStatus}`}>{facilities}</div>}
      </div>
      <p className={styles.muted} style={{ fontSize: 13 }}>
        <Icon name="map" size={14} /> Click a hazard or a truck on the map for details. Draw a what-if scenario in <Link href="/dashboard/simulation">What-If Simulation</Link>.
      </p>

      <EvacuateDialog hazardId={evacuateFor} onClose={() => setEvacuateFor(null)} onResult={(text, ok) => setNote({ text, ok })} />
    </>
  );
}
