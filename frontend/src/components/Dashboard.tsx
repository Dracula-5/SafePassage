"use client";

// Duty officer's screen: live numbers, the map, and what is on the road. Everything refreshes every 5
// seconds from small status calls. The map's hazard shapes are large, so they reload only when the server
// says they changed. The stream in OpsPanel pushes truck and plan changes faster than that.

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, FeatureCollection, HazardProperties, Overview, PlanDetail, plansApi, PublicStatus, regionBbox, Severity, SourceHealth, Vehicle } from "@/lib/api";
import { ago, sourceName, tonnes } from "@/lib/format";
import { useAccount } from "@/lib/session";

import AppShell, { Loading } from "./AppShell";
import LiveStatus, { REFRESH_MS, useNow } from "./LiveStatus";
import OpsPanel from "./OpsPanel";

export const SEVERITY_COLORS: Record<string, string> = {
  extreme: "#b3002d",
  severe: "#e8590c",
  moderate: "#f2b705",
  minor: "#4c9be8",
  unknown: "#868e96",
};

const LiveMap = dynamic(() => import("./LiveMap"), { ssr: false, loading: () => <div className="map map-loading">Loading map…</div> });

const HAZARD_TYPES = ["flood", "cyclone", "heavy_rain", "storm", "landslide", "wildfire", "heatwave", "other"];
const SEVERITIES: Severity[] = ["extreme", "severe", "moderate", "minor"];
const SIDE_TABS = ["Hazards", "Fleet", "Sources"] as const;

function Kpi({ label, value, note, tone }: { label: string; value: string | number; note?: string; tone?: "alert" | "warn" | "good" }) {
  return (
    <div className={`kpi ${tone ?? ""}`}>
      <span className="kpi-label">{label}</span>
      {/* The key restarts a brief highlight, so a number that really changed catches the eye. */}
      <b className="kpi-value" key={String(value)}>{value}</b>
      {note && <span className="kpi-note">{note}</span>}
    </div>
  );
}

export default function Dashboard() {
  const account = useAccount("operator");
  return account ? <Operations account={account} /> : <Loading />;
}

function Operations({ account }: { account: NonNullable<ReturnType<typeof useAccount>> }) {
  const [status, setStatus] = useState<PublicStatus | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [hazards, setHazards] = useState<FeatureCollection<HazardProperties> | null>(null);
  const [health, setHealth] = useState<SourceHealth[]>([]);
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);
  const [tab, setTab] = useState<(typeof SIDE_TABS)[number]>("Hazards");
  const [facilitiesStatus, setFacilitiesStatus] = useState("");
  const [drawing, setDrawing] = useState(false);
  const [vertices, setVertices] = useState<[number, number][]>([]);
  const [drill, setDrill] = useState({ label: "", hazard_type: "flood", severity: "severe" as Severity, ttl_minutes: 120 });
  const [drillMsg, setDrillMsg] = useState<string | null>(null);
  const [plan, setPlan] = useState<PlanDetail | null>(null);
  const bounds = useMemo(() => regionBbox(), []);
  const hazardStamp = useRef("");
  useNow(); // keeps "12s ago" counting between refreshes

  const refresh = useCallback(async () => {
    try {
      const [s, h, v, o] = await Promise.all([api.status(), api.sourceHealth(), plansApi.vehicles(), api.overview()]);
      if (s.hazards_changed !== hazardStamp.current) {
        setHazards(await api.hazards());
        hazardStamp.current = s.hazards_changed;
      }
      setStatus(s);
      setHealth(h);
      setVehicles(v);
      setOverview(o);
      setFailed(false);
      setUpdatedAt(Date.now());
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    const first = setTimeout(refresh, 0);
    const timer = setInterval(() => { if (!document.hidden) refresh(); }, REFRESH_MS);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [refresh]);

  const reloadHazards = useCallback(() => {
    hazardStamp.current = "";
    refresh();
  }, [refresh]);

  const bySource = useMemo(() => {
    const counts = new Map<string, number>();
    for (const f of hazards?.features ?? []) counts.set(f.properties.source, (counts.get(f.properties.source) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [hazards]);

  async function submitDrill() {
    if (vertices.length < 3 || !drill.label.trim()) return;
    try {
      await api.createDrill({ ...drill, label: drill.label.trim(), geometry: { type: "Polygon", coordinates: [[...vertices, vertices[0]]] } });
      setDrillMsg("Drill published. A plan is being made.");
      setDrawing(false);
      setVertices([]);
      reloadHazards();
    } catch (err) {
      setDrillMsg((err as Error).message);
    }
  }

  async function cancelDrill(id: string) {
    try {
      await api.cancelDrill(id);
      reloadHazards();
    } catch (err) {
      setDrillMsg((err as Error).message);
    }
  }

  async function revoke(v: Vehicle) {
    if (!window.confirm(`Take ${v.label} off the fleet? Its phone stops working, and any route it is on is replanned.`)) return;
    await plansApi.revokeVehicle(v.id).then(refresh, (err) => setDrillMsg(err.message));
  }

  const fleet = vehicles.filter((v) => v.status !== "maintenance").sort((a, b) => a.label.localeCompare(b.label));
  return (
    <AppShell account={account} title="Operations" status={<LiveStatus updatedAt={updatedAt} failed={failed} />}>
      {failed && <div className="banner danger">Cannot reach the server. These numbers may be out of date; retrying every 5 seconds.</div>}
      <section className="kpis" aria-label="Live summary">
        <Kpi label="Active alerts" value={status?.hazards_active ?? "–"} note={`${status?.hazards_severe ?? 0} severe or worse`}
          tone={status?.hazards_severe ? "warn" : undefined} />
        <Kpi label="Awaiting approval" value={overview?.plans_awaiting ?? "–"} note="plans need a decision" tone={overview?.plans_awaiting ? "alert" : undefined} />
        <Kpi label="Trucks online" value={overview ? `${overview.trucks_online}/${overview.trucks_total}` : "–"} note="reporting GPS" />
        <Kpi label="On the road" value={overview ? tonnes(overview.kg_on_routes) : "–"} note={`${overview?.routes_live ?? 0} live routes`} />
        <Kpi label="Orders delivered" value={overview?.orders_delivered ?? "–"} note={`${overview?.orders_open ?? 0} still open`} tone={overview?.orders_delivered ? "good" : undefined} />
        <Kpi label="Live data sources" value={status ? `${status.sources_fresh}/${status.sources_total}` : "–"} note="reporting on time"
          tone={status && status.sources_fresh < status.sources_total ? "warn" : "good"} />
      </section>

      <div className="workspace">
        <aside className="pane">
          <nav className="tabs" aria-label="Left panel">
            {SIDE_TABS.map((t) => <button key={t} className={tab === t ? "tab active" : "tab"} onClick={() => setTab(t)}>{t}</button>)}
          </nav>
          <div className="pane-body">
            {tab === "Hazards" && (
              <>
                <p className="muted small">{bySource.map(([src, n]) => `${sourceName(src)}: ${n}`).join(" · ") || "No active alerts."}</p>
                <ul className="list">
                  {(hazards?.features ?? []).slice(0, 40).map((f) => (
                    <li key={f.id} className="hazard">
                      <span className="swatch" style={{ background: SEVERITY_COLORS[f.properties.severity] }} />
                      <span>
                        <span className="tags">
                          <b className="tag-severity">{f.properties.severity}</b>
                          {f.properties.is_drill && <span className="badge drill">DRILL</span>}
                          {f.properties.is_stale && <span className="badge stale">STALE</span>}
                        </span>
                        {f.properties.headline.slice(0, 110)}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            )}
            {tab === "Fleet" && (
              <ul className="list">
                {fleet.map((v) => (
                  <li key={v.id} className="row-between">
                    <span>
                      <span className={`dot ${v.is_offline ? "stale" : "fresh"}`} />
                      <b>{v.label}</b>
                      <small className="block muted">{v.driver_name ? `${v.driver_name} · ` : ""}{v.is_offline ? "offline" : v.status} · GPS {ago(v.last_seen_at)}</small>
                    </span>
                    <button className="link danger" onClick={() => revoke(v)}>Remove</button>
                  </li>
                ))}
                {fleet.length === 0 && <li className="empty">No trucks yet. Drivers appear here when they sign up and register their vehicle.</li>}
              </ul>
            )}
            {tab === "Sources" && (
              <ul className="list">
                {health.map((s) => (
                  <li key={s.source}>
                    <span className={`dot ${s.is_stale ? "stale" : s.fallback_source ? "fallback" : "fresh"}`} />
                    <b>{sourceName(s.source)}</b>
                    <small className="block muted">last report {ago(s.last_success_at)}</small>
                    {s.is_stale && <span className="badge stale">DATA STALE</span>}
                    {s.fallback_source && <span className="badge fallback">using backup: {s.fallback_source}</span>}
                    {s.breaker_state !== "closed" && <span className="badge stale">paused after failures</span>}
                  </li>
                ))}
                <li className="empty">The screen refreshes every 5 seconds. Sources publish at their own pace: alerts about every 2 minutes, forecasts every few hours.</li>
              </ul>
            )}
          </div>
        </aside>

        <div className="map-area">
          <LiveMap
            hazards={hazards}
            initialBounds={bounds}
            drawing={drawing}
            draftVertices={vertices}
            onMapClick={(c) => setVertices((v) => [...v, c])}
            onCancelDrill={cancelDrill}
            onFacilitiesStatus={setFacilitiesStatus}
            vehicles={vehicles}
            routes={plan?.routes ?? []}
          />
          <div className="map-tools">
            {!drawing ? (
              <button className="primary" onClick={() => { setDrawing(true); setVertices([]); setDrillMsg(null); }}>Draw a what-if drill</button>
            ) : (
              <div className="drill-form">
                <b>New drill</b>
                <p className="small muted">Click the map to add corners ({vertices.length} so far, 3 needed).</p>
                <input placeholder="Name, e.g. Krishna embankment breach" value={drill.label} maxLength={200}
                  onChange={(e) => setDrill({ ...drill, label: e.target.value })} />
                <div className="row">
                  <select aria-label="Type" value={drill.hazard_type} onChange={(e) => setDrill({ ...drill, hazard_type: e.target.value })}>
                    {HAZARD_TYPES.map((t) => <option key={t}>{t}</option>)}
                  </select>
                  <select aria-label="Severity" value={drill.severity} onChange={(e) => setDrill({ ...drill, severity: e.target.value as Severity })}>
                    {SEVERITIES.map((s) => <option key={s}>{s}</option>)}
                  </select>
                </div>
                <div className="row">
                  <button className="primary" disabled={vertices.length < 3 || !drill.label.trim()} onClick={submitDrill}>Publish</button>
                  <button onClick={() => setVertices((v) => v.slice(0, -1))} disabled={!vertices.length}>Undo</button>
                  <button onClick={() => { setDrawing(false); setVertices([]); }}>Cancel</button>
                </div>
              </div>
            )}
            {drillMsg && <p className="map-note">{drillMsg}</p>}
          </div>
          <div className="legend" aria-hidden="true">
            {SEVERITIES.map((s) => <span key={s}><i style={{ background: SEVERITY_COLORS[s] }} />{s}</span>)}
          </div>
          {facilitiesStatus && <div className="map-status">{facilitiesStatus}</div>}
        </div>

        <OpsPanel selectedPlanId={plan?.id ?? null} onSelectPlan={setPlan} account={account}
          onVehicle={(v) => setVehicles((prev) => [...prev.filter((p) => p.id !== v.id), v])} onHazardChange={reloadHazards} />
      </div>
    </AppShell>
  );
}
