"use client";

// Duty officer's screen: live numbers, the map, and what is on the road. Everything refreshes every 5
// seconds from small status calls. The map's hazard shapes are large, so they reload only when the server
// says they changed. The stream in OpsPanel pushes truck and plan changes faster than that.

import dynamic from "next/dynamic";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  api, demoApi, DemoStatus, DriverRequest, FeatureCollection, HazardProperties, LiveRoute, Overview, PlanDetail, plansApi, PublicStatus, regionBbox,
  requestsApi, Severity, SourceHealth, Vehicle,
} from "@/lib/api";
import { ago, plate, sourceName, tonnes } from "@/lib/format";
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
export const SIMULATED_TRUCK = "#12263a";  // the ring on a white truck: a real truck's is teal
export const SUPPLY_ROUTE = "#f76707";
export const EVACUATION_ROUTE = "#2b8a3e";

const LiveMap = dynamic(() => import("./LiveMap"), { ssr: false, loading: () => <div className="map map-loading">Loading map…</div> });

const HAZARD_TYPES = ["flood", "cyclone", "heavy_rain", "storm", "landslide", "wildfire", "heatwave", "other"];
const SEVERITIES: Severity[] = ["extreme", "severe", "moderate", "minor"];
const SIDE_TABS = ["Hazards", "Fleet", "Sources"] as const;
const REQUEST_BADGES: Record<string, { text: string; tone: string }> = {
  sent: { text: "REQUEST SENT", tone: "replan" }, accepted: { text: "ACCEPTED", tone: "accepted" }, declined: { text: "DECLINED", tone: "stale" },
};

function away(metres: number | undefined): string {
  if (metres === undefined) return "";
  return metres === 0 ? "inside the area · " : `${(metres / 1000).toFixed(metres < 10_000 ? 1 : 0)} km away · `;
}

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

/** Presentation mode only. A driver with a real phone signs up at /signup instead. */
function AddDriver({ towns, onAdded }: { towns: { name: string }[]; onAdded: (message: string) => void }) {
  const [form, setForm] = useState({ driver_name: "", plate: "", capacity_kg: "8000", driver_language: "te", town: towns[0]?.name ?? "" });
  const [error, setError] = useState<string | null>(null);
  const field = (name: keyof typeof form) => ({
    value: form[name],
    onChange: (e: { target: { value: string } }) => setForm({ ...form, [name]: e.target.value }),
  });

  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await demoApi.addDriver({ ...form, driver_name: form.driver_name.trim(), plate: form.plate.trim(), capacity_kg: Number(form.capacity_kg) });
      onAdded(`${form.driver_name.trim()} added at ${form.town}. The truck appears on the map within a few seconds.`);
      setForm({ ...form, driver_name: "", plate: "" });
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <details className="add-driver">
      <summary>Add a driver</summary>
      <form className="form" onSubmit={submit}>
        <label>Driver&apos;s name<input required minLength={2} maxLength={100} {...field("driver_name")} /></label>
        <label>Vehicle number<input required minLength={2} maxLength={40} placeholder="AP 16 TX 1234" {...field("plate")} /></label>
        <div className="row">
          <label>Capacity (kg)<input required type="number" min={1} max={60000} {...field("capacity_kg")} /></label>
          <label>Language
            <select {...field("driver_language")}>
              <option value="te">Telugu</option>
              <option value="hi">Hindi</option>
              <option value="en">English</option>
            </select>
          </label>
        </div>
        <label>Starts from
          <select {...field("town")}>{towns.map((t) => <option key={t.name}>{t.name}</option>)}</select>
        </label>
        {error && <div className="alert danger small" role="alert">{error}</div>}
        <button className="primary">Add simulated driver</button>
      </form>
    </details>
  );
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
  const [demo, setDemo] = useState<DemoStatus | null>(null);
  const [fleetMsg, setFleetMsg] = useState<string | null>(null);
  const [gpsCut, setGpsCut] = useState<string[]>([]);
  const [place, setPlace] = useState("");
  const [distances, setDistances] = useState<Record<string, number>>({});
  const [requests, setRequests] = useState<DriverRequest[]>([]);
  const [liveRoutes, setLiveRoutes] = useState<LiveRoute[]>([]);
  const bounds = useMemo(() => regionBbox(), []);
  const hazardStamp = useRef("");
  useNow(); // keeps "12s ago" counting between refreshes

  const refresh = useCallback(async () => {
    try {
      const [s, h, v, o, d, r, l] = await Promise.all([api.status(), api.sourceHealth(), plansApi.vehicles(), api.overview(), demoApi.status(), requestsApi.list(), plansApi.liveRoutes()]);
      if (s.hazards_changed !== hazardStamp.current) {
        setHazards(await api.hazards());
        hazardStamp.current = s.hazards_changed;
      }
      setDemo(d);
      setRequests(r);
      setLiveRoutes(l);
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

  // Trucks move, so the distances are read again on every refresh.
  useEffect(() => {
    if (place) requestsApi.distances(place).then(setDistances, () => undefined);
  }, [place, updatedAt]);

  const places = useMemo(() => (hazards?.features ?? [])
    .filter((f) => f.properties.is_drill || f.properties.severity === "severe" || f.properties.severity === "extreme")
    .sort((a, b) => Number(b.properties.is_drill) - Number(a.properties.is_drill)), [hazards]);

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

  function findDrivers(hazardId: string) {
    setPlace(hazardId);
    setTab("Fleet");
    setFleetMsg(null);
  }

  async function ask(v: Vehicle) {
    setTab("Fleet");
    if (!place) return setFleetMsg(`Choose a place above first, then press Send request on truck ${v.fleet_number}.`);
    try {
      await requestsApi.send(v.id, place);
      setFleetMsg(`Request sent to truck ${v.fleet_number}${v.driver_name ? `, ${v.driver_name}` : ""}. It is on their screen now.`);
      refresh();
    } catch (err) {
      setFleetMsg((err as Error).message);
    }
  }

  async function evacuate(hazardId: string) {
    const people = window.prompt("How many people need to leave?", "40");
    if (!people) return;
    await plansApi.evacuate(hazardId, Number(people)).then(
      () => setDrillMsg("Evacuation requested. The nearest free truck is on its way: see the Evacuations tab."),
      (err) => setDrillMsg(err.message),
    );
  }

  async function planWithDriver() {
    try {
      await plansApi.planAgain([place], account.name);
      setFleetMsg("Planning now, with the drivers who accepted going first. The plan appears on the Plans tab.");
    } catch (err) {
      setFleetMsg((err as Error).message);
    }
  }

  async function cutGps(v: Vehicle, cut: boolean) {
    try {
      await demoApi.setMode(v.id, cut ? "gps_lost" : "normal");
      setGpsCut((ids) => (cut ? [...ids, v.id] : ids.filter((id) => id !== v.id)));
      setFleetMsg(cut ? `${plate(v.label)} has lost GPS. It shows as offline in 2 minutes. On a route, its goods move to another truck after 5.`
        : `${plate(v.label)} is reporting again.`);
    } catch (err) {
      setFleetMsg((err as Error).message);
    }
  }

  async function leaveRoute(v: Vehicle) {
    await demoApi.setMode(v.id, "off_route").then(
      () => setFleetMsg(`${plate(v.label)} is leaving its route. The Route Monitor notices within 20 seconds and sends a new one, with nobody pressing anything.`),
      (err) => setFleetMsg(err.message),
    );
  }

  const fleet = vehicles.filter((v) => v.status !== "maintenance")
    .sort((a, b) => (place ? (distances[a.id] ?? Infinity) - (distances[b.id] ?? Infinity) : 0)
      || Number(b.on_route) - Number(a.on_route) || Number(a.is_offline) - Number(b.is_offline) || a.fleet_number - b.fleet_number);
  const anyAccepted = place && requests.some((r) => r.hazard_id === place && r.status === "accepted");
  const simulated = fleet.filter((v) => v.simulated).length;
  return (
    <AppShell account={account} title="Operations" status={<LiveStatus updatedAt={updatedAt} failed={failed} />}>
      {failed && <div className="banner danger">Cannot reach the server. These numbers may be out of date; retrying every 5 seconds.</div>}
      {demo?.enabled && (
        <div className="banner demo">Presentation mode: {simulated} simulated trucks. Their drivers, number plates and GPS are made up, and so is the stock at depots marked TEST-SIM. Alerts, roads, ration stock and places are live.</div>
      )}
      <section className="kpis" aria-label="Live summary">
        <Kpi label="Active alerts" value={status?.hazards_active ?? "–"} note={`${status?.hazards_severe ?? 0} severe or worse`}
          tone={status?.hazards_severe ? "warn" : undefined} />
        <Kpi label="Awaiting approval" value={overview?.plans_awaiting ?? "–"} note="plans need a decision" tone={overview?.plans_awaiting ? "alert" : undefined} />
        <Kpi label="Trucks online" value={overview ? `${overview.trucks_online}/${overview.trucks_total}` : "–"} note={simulated ? `${simulated} simulated` : "reporting GPS"} />
        <Kpi label="On the road" value={overview ? tonnes(overview.kg_on_routes) : "–"} note={`${overview?.routes_live ?? 0} live routes`} />
        <Kpi label="Orders delivered" value={overview?.orders_delivered ?? "–"} note={`${overview?.orders_open ?? 0} still open`} tone={overview?.orders_delivered ? "good" : undefined} />
        <Kpi label="People evacuated" value={overview?.people_safe ?? "–"} note={`${overview?.people_waiting ?? 0} still to reach safety`}
          tone={overview?.people_waiting ? "warn" : overview?.people_safe ? "good" : undefined} />
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
              <>
                {demo?.enabled && <AddDriver towns={demo.towns ?? []} onAdded={setFleetMsg} />}
                <label className="ask">Send a driver to
                  <select value={place} onChange={(e) => findDrivers(e.target.value)}>
                    <option value="">Choose a drill or severe alert…</option>
                    {places.map((f) => <option key={f.id} value={f.id}>{f.properties.headline.slice(0, 60)}</option>)}
                  </select>
                </label>
                {place && <p className="small muted">Nearest first. The numbers match the round buttons on the map.</p>}
                {fleetMsg && <div className="alert small">{fleetMsg}</div>}
                {anyAccepted && <button className="primary" onClick={planWithDriver}>Make a plan with accepted drivers</button>}
                <ul className="list">
                  {fleet.map((v) => {
                    const cut = gpsCut.includes(v.id) || v.is_offline;
                    const asked = place ? requests.find((r) => r.vehicle_id === v.id && r.hazard_id === place && r.status !== "withdrawn") : undefined;
                    const badge = asked && REQUEST_BADGES[asked.status];
                    return (
                      <li key={v.id} className="truck">
                        <span>
                          <span className="truck-no">{v.fleet_number}</span>
                          <span className={`dot ${v.is_offline ? "stale" : "fresh"}`} />
                          <b>{plate(v.label)}</b>
                          {v.simulated && <span className="badge sim">SIMULATED</span>}
                          {v.on_route && <span className="badge replan">ON ROUTE</span>}
                          {badge && <span className={`badge ${badge.tone}`}>{badge.text}</span>}
                        </span>
                        <small className="block muted">{place ? away(distances[v.id]) : ""}{v.driver_name ? `${v.driver_name} · ` : ""}{tonnes(v.capacity_kg)} · {v.is_offline ? "offline" : "online"} · GPS {ago(v.last_seen_at)}</small>
                        <div className="row">
                          {place && !v.on_route && !v.is_offline && !asked && <button className="link" onClick={() => ask(v)}>Send request</button>}
                          {asked?.status === "declined" && !v.on_route && !v.is_offline && <button className="link" onClick={() => ask(v)}>Ask again</button>}
                          {asked?.status === "sent" && <button className="link" onClick={() => requestsApi.withdraw(asked.id).then(refresh)}>Withdraw</button>}
                          <a className="link" href={`/driver-view?vehicle=${v.id}`} target="_blank" rel="noreferrer">Driver screen</a>
                          {v.simulated && <button className="link" onClick={() => cutGps(v, !cut)}>{cut ? "Restore GPS" : "Lose GPS"}</button>}
                          {v.simulated && v.on_route && !cut && <button className="link" onClick={() => leaveRoute(v)}>Leave route</button>}
                          <button className="link danger" onClick={() => revoke(v)}>Remove</button>
                        </div>
                      </li>
                    );
                  })}
                  {fleet.length === 0 && <li className="empty">No trucks yet. Drivers appear here when they sign up and register their vehicle.</li>}
                </ul>
              </>
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
            onFindDrivers={findDrivers}
            onAskTruck={(id) => { const truck = vehicles.find((v) => v.id === id); if (truck) ask(truck); }}
            onEvacuate={evacuate}
            vehicles={vehicles}
            routes={plan?.routes ?? []}
            liveRoutes={liveRoutes}
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
            <span><i className="line" style={{ background: SUPPLY_ROUTE }} />supply route</span>
            <span><i className="line" style={{ background: EVACUATION_ROUTE }} />evacuation route</span>
            <span><i className="round" style={{ background: "#fff", boxShadow: `0 0 0 2px ${SIMULATED_TRUCK}` }} />{simulated > 0 ? "simulated truck" : "truck"}</span>
          </div>
          {facilitiesStatus && <div className="map-status">{facilitiesStatus}</div>}
        </div>

        <OpsPanel selectedPlanId={plan?.id ?? null} onSelectPlan={setPlan} account={account} vehicles={vehicles}
          onVehicle={(v) => setVehicles((prev) => [...prev.filter((p) => p.id !== v.id), v])} onHazardChange={reloadHazards} />
      </div>
    </AppShell>
  );
}
