"use client";

// The fleet: every truck that has registered, whether it is reporting GPS, and whether it is on a route. A duty
// officer can ask the nearest drivers to wait at the edge of a hazard. Positions and times come from the trucks.

import dynamic from "next/dynamic";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, useEffect, useMemo, useState } from "react";

import { demoApi, DriverRequest, PlanRoute, plansApi, requestsApi, Vehicle } from "@/lib/api";
import { ago, plate, tonnes } from "@/lib/format";

import { ActionButton, AlertCard, ConfirmDialog, Column, DataTable, DetailDrawer, EmptyState, FilterBar, FilterChips, PageHeader, SearchInput, StatusBadge, Surface } from "../ui";
import { away, matches } from "./constants";
import styles from "./ops.module.css";
import { useOps } from "./OpsProvider";

const LiveMap = dynamic(() => import("../LiveMap"), { ssr: false, loading: () => <div className="map map-loading">Loading map…</div> });
const NO_ROUTES: PlanRoute[] = [];

const REQUEST_LABEL: Record<string, { text: string; tone: "info" | "good" | "bad" }> = {
  sent: { text: "Request sent", tone: "info" }, accepted: { text: "Accepted", tone: "good" }, declined: { text: "Declined", tone: "bad" },
};

type StatusFilter = "all" | "online" | "offline" | "route";

/** Presentation mode only. A driver with a real phone signs up at /signup instead. */
function AddDriver({ towns, onAdded }: { towns: { name: string }[]; onAdded: (message: string, ok: boolean) => void }) {
  const [form, setForm] = useState({ driver_name: "", plate: "", capacity_kg: "8000", driver_language: "te", town: towns[0]?.name ?? "" });
  const [busy, setBusy] = useState(false);
  const field = (name: keyof typeof form) => ({ value: form[name], onChange: (e: { target: { value: string } }) => setForm({ ...form, [name]: e.target.value }) });

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await demoApi.addDriver({ ...form, driver_name: form.driver_name.trim(), plate: form.plate.trim(), capacity_kg: Number(form.capacity_kg) });
      onAdded(`${form.driver_name.trim()} added at ${form.town}. The truck appears on the map within a few seconds.`, true);
      setForm({ ...form, driver_name: "", plate: "" });
    } catch (err) {
      onAdded((err as Error).message, false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="card" style={{ padding: "14px 18px" }}>
      <summary style={{ cursor: "pointer", fontWeight: 600 }}>Presentation tools: add a simulated driver</summary>
      <form className="form" onSubmit={submit}>
        <label>Driver&apos;s name<input required minLength={2} maxLength={100} {...field("driver_name")} /></label>
        <label>Vehicle number<input required minLength={2} maxLength={40} placeholder="AP 16 TX 1234" {...field("plate")} /></label>
        <div className="row">
          <label>Capacity (kg)<input required type="number" min={1} max={60000} {...field("capacity_kg")} /></label>
          <label>Language
            <select {...field("driver_language")}><option value="te">Telugu</option><option value="hi">Hindi</option><option value="en">English</option></select>
          </label>
        </div>
        <label>Starts from<select {...field("town")}>{towns.map((t) => <option key={t.name}>{t.name}</option>)}</select></label>
        <ActionButton variant="primary" type="submit" busy={busy}>Add simulated driver</ActionButton>
      </form>
    </details>
  );
}

export default function FleetPage() {
  const router = useRouter();
  const params = useSearchParams();
  const place = params.get("place") ?? "";
  const { account, vehicles, hazards, requests, liveRoutes, demo, bounds, updatedAt, refresh } = useOps();
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [openId, setOpenId] = useState<string | null>(params.get("truck"));
  const [distances, setDistances] = useState<Record<string, number>>({});
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);
  const [removing, setRemoving] = useState<Vehicle | null>(null);
  const [busy, setBusy] = useState(false);
  const [gpsCut, setGpsCut] = useState<string[]>([]);

  // Trucks move, so the distances are read again on every refresh.
  useEffect(() => {
    if (!place) return;
    let current = true;
    requestsApi.distances(place).then((d) => { if (current) setDistances(d); }, () => undefined);
    return () => { current = false; };
  }, [place, updatedAt]);

  const places = useMemo(() => (hazards?.features ?? []).map((f) => f.properties)
    .filter((h) => h.is_drill || h.severity === "severe" || h.severity === "extreme")
    .sort((a, b) => Number(b.is_drill) - Number(a.is_drill)), [hazards]);
  const chosen = places.find((h) => h.id === place);
  const requestFor = (v: Vehicle): DriverRequest | undefined => place ? requests.find((r) => r.vehicle_id === v.id && r.hazard_id === place && r.status !== "withdrawn") : undefined;
  const anyAccepted = place !== "" && requests.some((r) => r.hazard_id === place && r.status === "accepted");

  const fleet = useMemo(() => vehicles.filter((v) => v.status !== "maintenance")
    .filter((v) => matches(query, v.label, v.driver_name, String(v.fleet_number)))
    .filter((v) => filter === "all" || (filter === "online" && !v.is_offline) || (filter === "offline" && v.is_offline) || (filter === "route" && v.on_route))
    .sort((a, b) => (place ? (distances[a.id] ?? Infinity) - (distances[b.id] ?? Infinity) : 0)
      || Number(b.on_route) - Number(a.on_route) || Number(a.is_offline) - Number(b.is_offline) || a.fleet_number - b.fleet_number),
  [vehicles, query, filter, place, distances]);
  const all = vehicles.filter((v) => v.status !== "maintenance");
  const open = vehicles.find((v) => v.id === openId) ?? null;
  const simulated = all.filter((v) => v.simulated).length;
  const openVehicles = useMemo(() => (open ? [open] : []), [open]);
  const routesOfOpen = useMemo(() => (open ? liveRoutes.filter((r) => r.vehicle_id === open.id) : []), [open, liveRoutes]);

  function choosePlace(id: string) {
    router.replace(id ? `/dashboard/fleet?place=${id}` : "/dashboard/fleet", { scroll: false });
    setMsg(null);
  }

  async function run(action: () => Promise<unknown>, success: string) {
    setBusy(true);
    try {
      await action();
      setMsg({ text: success, ok: true });
      await refresh();
    } catch (err) {
      setMsg({ text: (err as Error).message, ok: false });
    } finally {
      setBusy(false);
    }
  }

  const ask = (v: Vehicle) => place
    ? run(() => requestsApi.send(v.id, place), `Request sent to truck ${v.fleet_number}${v.driver_name ? `, ${v.driver_name}` : ""}. It is on their screen now.`)
    : setMsg({ text: `Choose a place above first, then press Send request on truck ${v.fleet_number}.`, ok: false });
  const cutGps = (v: Vehicle, cut: boolean) => run(async () => {
    await demoApi.setMode(v.id, cut ? "gps_lost" : "normal");
    setGpsCut((ids) => (cut ? [...ids, v.id] : ids.filter((id) => id !== v.id)));
  }, cut ? `${plate(v.label)} has lost GPS. It shows as offline in 2 minutes. On a route, its goods move to another truck after 5.` : `${plate(v.label)} is reporting again.`);
  const leaveRoute = (v: Vehicle) => run(() => demoApi.setMode(v.id, "off_route"),
    `${plate(v.label)} is leaving its route. The Route Monitor notices within 20 seconds and sends a new one, with nobody pressing anything.`);

  const columns: Column<Vehicle>[] = [
    { key: "no", header: "No.", render: (v) => <span className="truck-no">{v.fleet_number}</span> },
    { key: "truck", header: "Truck", render: (v) => (
      <span className={styles.cell}>
        <b>{plate(v.label)}{v.simulated && <span className="badge sim">SIMULATED</span>}</b>
        <small>{v.driver_name ?? "Driver name not given"}</small>
      </span>
    ) },
    { key: "capacity", header: "Capacity", numeric: true, render: (v) => tonnes(v.capacity_kg) },
    { key: "status", header: "Status", render: (v) => (
      <span className={styles.tags}>
        <StatusBadge tone={v.is_offline ? "bad" : "good"}>{v.is_offline ? "Offline" : "Online"}</StatusBadge>
        {v.on_route && <StatusBadge tone="info">On a route</StatusBadge>}
      </span>
    ) },
    { key: "gps", header: "Last GPS", render: (v) => <span className="small">{ago(v.last_seen_at)}</span> },
    ...(place ? [{
      key: "ask", header: "Request", render: (v: Vehicle) => {
        const r = requestFor(v);
        const label = r && REQUEST_LABEL[r.status];
        return (
          <span className={styles.cell} onClick={(e) => e.stopPropagation()}>
            <small>{away(distances[v.id])}</small>
            {label ? <StatusBadge tone={label.tone}>{label.text}</StatusBadge> : null}
            {!v.on_route && !v.is_offline && (!r || r.status === "declined") && <button type="button" className="small" disabled={busy} onClick={() => ask(v)}>{r ? "Ask again" : "Send request"}</button>}
            {r?.status === "sent" && <button type="button" className="small" disabled={busy} onClick={() => run(() => requestsApi.withdraw(r.id), "Request withdrawn.")}>Withdraw</button>}
          </span>
        );
      },
    } as Column<Vehicle>] : []),
  ];

  const cut = open ? gpsCut.includes(open.id) || open.is_offline : false;
  return (
    <>
      <PageHeader title="Fleet & Drivers" subtitle="Every registered truck, its connection, and whether it is on a route." />
      {msg && <AlertCard tone={msg.ok ? "good" : "bad"}>{msg.text}</AlertCard>}

      <Surface title="Send a driver to a place">
        <label style={{ display: "grid", gap: 6, fontWeight: 600, fontSize: 13.5, maxWidth: 560 }}>
          Choose a drill or severe alert
          <select value={place} onChange={(e) => choosePlace(e.target.value)}>
            <option value="">No place chosen</option>
            {places.map((h) => <option key={h.id} value={h.id}>{h.is_drill ? "DRILL · " : ""}{h.headline.slice(0, 80)}</option>)}
          </select>
        </label>
        {place && <p className={styles.muted} style={{ marginTop: 8, fontSize: 13 }}>Nearest first, for &ldquo;{chosen?.headline.slice(0, 70) ?? "this place"}&rdquo;. A request asks the driver to wait at the edge of the zone.</p>}
        {anyAccepted && (
          <div style={{ marginTop: 12 }}>
            <ActionButton variant="primary" icon="plans" busy={busy} onClick={() => run(() => plansApi.planAgain([place], account.name), "Planning now, with the drivers who accepted going first. The plan appears on Plans & Approvals.")}>
              Make a plan with accepted drivers
            </ActionButton>
          </div>
        )}
      </Surface>

      {demo?.enabled && <AddDriver towns={demo.towns ?? []} onAdded={(text, ok) => setMsg({ text, ok })} />}

      <FilterBar>
        <SearchInput value={query} onChange={setQuery} placeholder="Search a driver, plate or truck number" />
        <FilterChips label="Truck status" value={filter} onChange={setFilter} options={[
          { key: "all", label: "All", count: all.length }, { key: "online", label: "Online", count: all.filter((v) => !v.is_offline).length },
          { key: "offline", label: "Offline", count: all.filter((v) => v.is_offline).length }, { key: "route", label: "On a route", count: all.filter((v) => v.on_route).length },
        ]} />
      </FilterBar>

      <Surface flush>
        <DataTable caption="Trucks" columns={columns} rows={fleet} rowKey={(v) => v.id} onRowClick={(v) => setOpenId(v.id)} selectedKey={openId}
          empty={<EmptyState icon="truck" title={all.length === 0 ? "No trucks yet" : "No trucks match"}>
            {all.length === 0 ? "Drivers appear here when they sign up and register their vehicle." : "Try a different search or status."}</EmptyState>} />
      </Surface>
      {simulated > 0 && <p className={styles.muted} style={{ fontSize: 13 }}>{simulated} of {all.length} trucks are simulated for the presentation.</p>}

      <DetailDrawer open={open !== null} title={open ? `Truck ${open.fleet_number} · ${plate(open.label)}` : "Truck"} onClose={() => setOpenId(null)}>
        {open && (
          <>
            <span className={styles.tags}>
              <StatusBadge tone={open.is_offline ? "bad" : "good"}>{open.is_offline ? "Offline" : "Online"}</StatusBadge>
              {open.on_route && <StatusBadge tone="info">On a route</StatusBadge>}
              {open.simulated && <StatusBadge tone="warn">SIMULATED</StatusBadge>}
            </span>
            <dl className={styles.facts}>
              <div className={styles.fact}><small>Driver</small><b>{open.driver_name ?? "Not given"}</b></div>
              <div className={styles.fact}><small>Capacity</small><b>{tonnes(open.capacity_kg)}</b></div>
              <div className={styles.fact}><small>Last GPS</small><b>{ago(open.last_seen_at)}</b></div>
              <div className={styles.fact}><small>Position</small><b>{open.position ? `${open.position.coordinates[1].toFixed(4)}, ${open.position.coordinates[0].toFixed(4)}` : "No position yet"}</b></div>
              <div className={styles.fact}><small>Route</small><b>{open.on_route ? "On an active route" : "Not on a route"}</b></div>
            </dl>
            <div className={`${styles.stage} ${styles.stageShort}`}>
              <LiveMap hazards={hazards} initialBounds={bounds} drawing={false} draftVertices={[]} onMapClick={() => undefined} onCancelDrill={() => undefined}
                onFacilitiesStatus={() => undefined} onFindDrivers={() => undefined} onAskTruck={() => undefined} onEvacuate={() => undefined}
                vehicles={openVehicles} routes={NO_ROUTES} liveRoutes={routesOfOpen} />
            </div>
            {routesOfOpen.length === 0 && <p className={styles.muted} style={{ fontSize: 13 }}>No live route to draw for this truck.</p>}
            <div className="row">
              {place && !open.on_route && !open.is_offline && !requestFor(open) && <ActionButton variant="primary" busy={busy} onClick={() => ask(open)}>Send request</ActionButton>}
              <a className="button" href={`/driver-view?vehicle=${open.id}`} target="_blank" rel="noreferrer">Driver screen</a>
              {open.simulated && <ActionButton busy={busy} onClick={() => cutGps(open, !cut)}>{cut ? "Restore GPS" : "Lose GPS"}</ActionButton>}
              {open.simulated && open.on_route && !cut && <ActionButton busy={busy} onClick={() => leaveRoute(open)}>Leave route</ActionButton>}
              <ActionButton variant="danger" onClick={() => setRemoving(open)}>Remove from fleet</ActionButton>
            </div>
          </>
        )}
      </DetailDrawer>

      <ConfirmDialog open={removing !== null} danger title="Remove this truck?" confirmLabel="Remove truck" busy={busy} onCancel={() => setRemoving(null)}
        onConfirm={async () => {
          if (!removing) return;
          await run(() => plansApi.revokeVehicle(removing.id), `${plate(removing.label)} is off the fleet.`);
          setRemoving(null);
          setOpenId(null);
        }}>
        <p>{removing ? `${plate(removing.label)}: ` : ""}its phone stops working, and any route it is on is replanned.</p>
      </ConfirmDialog>
    </>
  );
}
