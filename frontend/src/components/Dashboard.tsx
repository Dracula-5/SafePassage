"use client";

// Operations dashboard: live map + source freshness + active hazards + drill tool.
// Polls the backend every 30 s (the SSE stream replaces polling in Gate 2).

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";

import { api, FeatureCollection, HazardProperties, PlanDetail, plansApi, regionBbox, Severity, SourceHealth, Vehicle } from "@/lib/api";

import { SEVERITY_COLORS } from "./LiveMap";
import OpsPanel from "./OpsPanel";

const LiveMap = dynamic(() => import("./LiveMap"), { ssr: false, loading: () => <div className="map loading">Loading map…</div> });

const POLL_MS = 30_000;
const HAZARD_TYPES = ["flood", "cyclone", "heavy_rain", "storm", "landslide", "wildfire", "heatwave", "other"];
const SEVERITIES: Severity[] = ["extreme", "severe", "moderate", "minor"];

function ago(iso: string | null): string {
  if (!iso) return "never";
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 90) return `${s}s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

export default function Dashboard() {
  const [hazards, setHazards] = useState<FeatureCollection<HazardProperties> | null>(null);
  const [health, setHealth] = useState<SourceHealth[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [facilitiesStatus, setFacilitiesStatus] = useState("");
  const [drawing, setDrawing] = useState(false);
  const [vertices, setVertices] = useState<[number, number][]>([]);
  const [drill, setDrill] = useState({ label: "", hazard_type: "flood", severity: "severe" as Severity, ttl_minutes: 120 });
  const [drillMsg, setDrillMsg] = useState<string | null>(null);
  const bounds = useMemo(() => regionBbox(), []);
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [plan, setPlan] = useState<PlanDetail | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [h, s, v] = await Promise.all([api.hazards(), api.sourceHealth(), plansApi.vehicles()]);
      setHazards(h);
      setHealth(s);
      setVehicles(v);
      setError(null);
      setUpdatedAt(new Date());
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  useEffect(() => {
    // Poll the backend; setState happens after the awaited fetch, not synchronously in the effect.
    const first = setTimeout(refresh, 0);
    const timer = setInterval(refresh, POLL_MS);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [refresh]);

  const counts = useMemo(() => {
    const bySource = new Map<string, number>();
    for (const f of hazards?.features ?? []) bySource.set(f.properties.source, (bySource.get(f.properties.source) ?? 0) + 1);
    return [...bySource.entries()].sort((a, b) => b[1] - a[1]);
  }, [hazards]);

  const topHazards = useMemo(() => (hazards?.features ?? []).slice(0, 12), [hazards]);

  async function submitDrill() {
    if (vertices.length < 3 || !drill.label.trim()) return;
    try {
      await api.createDrill({ ...drill, label: drill.label.trim(), geometry: { type: "Polygon", coordinates: [[...vertices, vertices[0]]] } });
      setDrillMsg("Drill hazard created and published (hazard.updated).");
      setDrawing(false);
      setVertices([]);
      await refresh();
    } catch (err) {
      setDrillMsg((err as Error).message);
    }
  }

  async function cancelDrill(id: string) {
    try {
      await api.cancelDrill(id);
      await refresh();
    } catch (err) {
      setDrillMsg((err as Error).message);
    }
  }

  return (
    <div className="layout">
      <aside className="panel">
        <h1>ReliefRoute</h1>
        <p className="muted">Live hazards and relief facilities{updatedAt ? ` · updated ${updatedAt.toLocaleTimeString()}` : ""}</p>
        {error && <div className="error">Backend unreachable: {error}</div>}

        <section>
          <h2>Data sources</h2>
          {health.length === 0 && !error && <p className="muted">No source has reported yet.</p>}
          <ul className="sources">
            {health.map((s) => (
              <li key={s.source}>
                <span className={`dot ${s.is_stale ? "stale" : s.fallback_source ? "fallback" : "fresh"}`} />
                <b>{s.source}</b> <span className="muted">ok {ago(s.last_success_at)}</span>
                {s.is_stale && <span className="badge stale">DATA STALE</span>}
                {s.fallback_source && <span className="badge fallback">fallback: {s.fallback_source}</span>}
                {s.breaker_state !== "closed" && <span className="badge stale">breaker {s.breaker_state}</span>}
                {s.last_error && <div className="muted small">{s.last_error.slice(0, 140)}</div>}
              </li>
            ))}
          </ul>
        </section>

        <section>
          <h2>Fleet ({vehicles.filter((v) => !v.is_offline).length}/{vehicles.length} online)</h2>
          <ul className="sources">
            {vehicles.map((v) => (
              <li key={v.id}>
                <span className={`dot ${v.is_offline ? "stale" : "fresh"}`} />
                <b>{v.label}</b> <span className="muted">{v.is_offline ? "offline" : "online"} · last GPS {ago(v.last_seen_at)}</span>
              </li>
            ))}
            {vehicles.length === 0 && <li className="muted small">No vehicles registered. Drivers register in the Driver app.</li>}
          </ul>
        </section>

        <section>
          <h2>Active hazards ({hazards?.features.length ?? "…"})</h2>
          <p className="muted small">{counts.map(([src, n]) => `${src}: ${n}`).join(" · ") || "none"}</p>
          <ul className="hazards">
            {topHazards.map((f) => (
              <li key={f.id}>
                <span className="swatch" style={{ background: SEVERITY_COLORS[f.properties.severity] }} />
                {f.properties.is_drill && <span className="badge drill">DRILL</span>}
                {f.properties.is_stale && <span className="badge stale">STALE</span>}
                <span className="small">{f.properties.headline.slice(0, 90)}</span>
              </li>
            ))}
          </ul>
        </section>

        <section>
          <h2>What-if drill</h2>
          {!drawing ? (
            <button onClick={() => { setDrawing(true); setVertices([]); setDrillMsg(null); }}>Draw drill zone</button>
          ) : (
            <div className="drill-form">
              <p className="small">Click the map to add corners ({vertices.length} so far).</p>
              <input placeholder="Label, e.g. Krishna embankment breach" value={drill.label} maxLength={200}
                onChange={(e) => setDrill({ ...drill, label: e.target.value })} />
              <select value={drill.hazard_type} onChange={(e) => setDrill({ ...drill, hazard_type: e.target.value })}>
                {HAZARD_TYPES.map((t) => <option key={t}>{t}</option>)}
              </select>
              <select value={drill.severity} onChange={(e) => setDrill({ ...drill, severity: e.target.value as Severity })}>
                {SEVERITIES.map((s) => <option key={s}>{s}</option>)}
              </select>
              <label className="small">Expires after (min)
                <input type="number" min={5} max={1440} value={drill.ttl_minutes}
                  onChange={(e) => setDrill({ ...drill, ttl_minutes: Number(e.target.value) })} />
              </label>
              <div className="row">
                <button disabled={vertices.length < 3 || !drill.label.trim()} onClick={submitDrill}>Publish drill</button>
                <button onClick={() => setVertices((v) => v.slice(0, -1))} disabled={!vertices.length}>Undo</button>
                <button onClick={() => { setDrawing(false); setVertices([]); }}>Cancel</button>
              </div>
            </div>
          )}
          {drillMsg && <p className="small">{drillMsg}</p>}
        </section>
        <p className="muted small">{facilitiesStatus}</p>
      </aside>
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
      <OpsPanel
        selectedPlanId={plan?.id ?? null}
        onSelectPlan={setPlan}
        onVehicle={(v) => setVehicles((prev) => [...prev.filter((p) => p.id !== v.id), v])}
        onHazardChange={refresh}
      />
    </div>
  );
}
