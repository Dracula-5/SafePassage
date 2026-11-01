"use client";

// Everyone the system knows, for a duty officer: every truck and its driver, every supplier and what it
// holds, every warehouse, and everyone with an account. Most suppliers come from AePDS, so they have no
// account to appear under.

import { useCallback, useEffect, useMemo, useState } from "react";

import { Account, api, FacilityProperties, FeatureCollection, plansApi, SupplierListing, teamApi, Vehicle } from "@/lib/api";
import { ago, itemName, plate, tonnes } from "@/lib/format";
import { useAccount } from "@/lib/session";

import AppShell, { Loading } from "./AppShell";
import LiveStatus, { REFRESH_MS } from "./LiveStatus";
import TeamPanel from "./TeamPanel";

const TABS = ["Drivers", "Suppliers", "Warehouses", "People"] as const;
const SOURCE_NAMES: Record<string, string> = { osm: "OpenStreetMap", datagov: "data.gov.in", portal: "Registered here" };
const MAX_ROWS = 100; // a search narrows the rest; a table of hundreds helps nobody

export default function Directory() {
  const account = useAccount("operator");
  return account ? <Lists account={account} /> : <Loading />;
}

function matches(query: string, ...fields: (string | null | undefined)[]): boolean {
  const wanted = query.trim().toLowerCase();
  return !wanted || fields.some((f) => f?.toLowerCase().includes(wanted));
}

function Lists({ account }: { account: Account }) {
  const [tab, setTab] = useState<(typeof TABS)[number]>("Drivers");
  const [query, setQuery] = useState("");
  const [vehicles, setVehicles] = useState<Vehicle[] | null>(null);
  const [suppliers, setSuppliers] = useState<SupplierListing[] | null>(null);
  const [warehouses, setWarehouses] = useState<FeatureCollection<FacilityProperties> | null>(null);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [v, s] = await Promise.all([plansApi.vehicles(), teamApi.suppliers()]);
      setVehicles(v);
      setSuppliers(s);
      setFailed(false);
      setUpdatedAt(Date.now());
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    const first = setTimeout(load, 0);
    const timer = setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [load]);

  const trucks = useMemo(() => (vehicles ?? [])
    .filter((v) => v.status !== "maintenance" && matches(query, v.label, v.driver_name, String(v.fleet_number)))
    .sort((a, b) => Number(a.simulated) - Number(b.simulated) || Number(b.on_route) - Number(a.on_route) || plate(a.label).localeCompare(plate(b.label))),
  [vehicles, query]);
  const shops = useMemo(() => (suppliers ?? []).filter((s) => matches(query, s.name, s.site)), [suppliers, query]);
  const stores = useMemo(() => (warehouses?.features ?? []).map((f) => f.properties)
    .filter((w) => matches(query, w.name, w.kind))
    .sort((a, b) => Number(b.is_supplier) - Number(a.is_supplier) || a.name.localeCompare(b.name)), [warehouses, query]);

  // They rarely change and the list is long, so this is read once, when the tab is opened.
  useEffect(() => {
    if (tab === "Warehouses" && !warehouses) api.warehouses().then(setWarehouses, () => setMsg("The warehouse list could not be loaded. Try again in a moment."));
  }, [tab, warehouses]);

  async function setVerified(s: SupplierListing) {
    const leaving = s.verified && !window.confirm(`Withdraw verification from ${s.name}? Its stock link stops working too.`);
    if (leaving) return;
    await teamApi.verifySupplier(s.id, !s.verified).then(load, (err) => setMsg(err.message));
  }

  return (
    <AppShell account={account} title="Directory" status={<LiveStatus updatedAt={updatedAt} failed={failed} />}>
      <main className="page">
        <div className="page-head">
          <nav className="tabs" aria-label="Directory">
            {TABS.map((t) => (
              <button key={t} className={tab === t ? "tab active" : "tab"} onClick={() => setTab(t)}>
                {t}{t === "Drivers" && vehicles && <span className="count">{vehicles.filter((v) => v.status !== "maintenance").length}</span>}
                {t === "Suppliers" && suppliers && <span className="count">{suppliers.length}</span>}
                {t === "Warehouses" && warehouses && <span className="count">{warehouses.features.length}</span>}
              </button>
            ))}
          </nav>
          {tab !== "People" && (
            <input className="search" type="search" aria-label="Search" placeholder={tab === "Drivers" ? "Search a driver, plate or truck number" : tab === "Warehouses" ? "Search a warehouse or godown" : "Search a supplier or place"}
              value={query} onChange={(e) => setQuery(e.target.value)} />
          )}
        </div>
        {msg && <div className="alert danger small">{msg}</div>}

        {tab === "Drivers" && (vehicles === null ? <Loading /> : (
          <section className="card">
            <div className="table-wrap">
              <table>
                <thead><tr><th>No.</th><th>Truck</th><th>Driver</th><th className="num">Capacity</th><th>Status</th><th>Last GPS</th><th /></tr></thead>
                <tbody>
                  {trucks.map((v) => (
                    <tr key={v.id}>
                      <td><span className="truck-no">{v.fleet_number}</span></td>
                      <td><b>{plate(v.label)}</b>{v.simulated && <span className="badge sim">SIMULATED</span>}</td>
                      <td>{v.driver_name ?? <span className="muted">Not given</span>}</td>
                      <td className="num">{tonnes(v.capacity_kg)}</td>
                      <td>
                        <span className={`status ${v.is_offline ? "red" : v.on_route ? "blue" : "green"}`}>{v.is_offline ? "Offline" : v.on_route ? "On a route" : "Waiting"}</span>
                      </td>
                      <td className="muted small">{ago(v.last_seen_at)}</td>
                      <td><a className="link" href={`/driver-view?vehicle=${v.id}`} target="_blank" rel="noreferrer">Driver screen</a></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {trucks.length === 0 && <p className="empty">{query ? "Nobody matches that search." : "No trucks yet."}</p>}
            </div>
          </section>
        ))}

        {tab === "Suppliers" && (suppliers === null ? <Loading /> : (
          <section className="card">
            {shops.length > MAX_ROWS && <p className="muted small">Showing {MAX_ROWS} of {shops.length.toLocaleString()}. Search to find the rest.</p>}
            <div className="table-wrap">
              <table>
                <thead><tr><th>Supplier</th><th>What it can give now</th><th>Reported</th><th>Status</th></tr></thead>
                <tbody>
                  {shops.slice(0, MAX_ROWS).map((s) => (
                    <tr key={s.id}>
                      <td>
                        <b>{s.name}</b>{s.name.startsWith("TEST-SIM") && <span className="badge sim">SIMULATED</span>}
                        {s.site !== s.name && <small className="block muted">{s.site}</small>}
                        {s.contact && <small className="block muted">{s.contact}</small>}
                      </td>
                      <td>
                        {s.stock.filter((i) => i.available > 0).map((i) => `${Math.round(i.available).toLocaleString()} ${itemName(i.item_code)}`).join(", ")
                          || <span className="muted">Nothing available</span>}
                      </td>
                      <td className="muted small">{ago(s.reported_at)}</td>
                      <td>
                        <span className={s.verified ? "status green" : "status orange"}>{s.verified ? "Verified" : "Unverified"}</span>
                        <button className="link block" onClick={() => setVerified(s)}>{s.verified ? "Withdraw" : "Verify"}</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {shops.length === 0 && <p className="empty">{query ? "Nobody matches that search." : "No suppliers yet."}</p>}
            </div>
          </section>
        ))}

        {tab === "Warehouses" && (warehouses === null ? <Loading /> : (
          <section className="card">
            {stores.length > MAX_ROWS && <p className="muted small">Showing {MAX_ROWS} of {stores.length.toLocaleString()}. Search to find the rest.</p>}
            <div className="table-wrap">
              <table>
                <thead><tr><th>Name</th><th>Type</th><th>Supplier here?</th><th>From</th></tr></thead>
                <tbody>
                  {stores.slice(0, MAX_ROWS).map((w) => (
                    <tr key={w.id}>
                      <td><b>{w.name}</b>{w.name.startsWith("TEST-SIM") && <span className="badge sim">SIMULATED</span>}</td>
                      <td className="capitalize">{w.kind}</td>
                      <td>{w.is_supplier ? <span className="status green">Yes</span> : <span className="muted">No</span>}</td>
                      <td className="muted small">{SOURCE_NAMES[w.source] ?? w.source}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {stores.length === 0 && <p className="empty">{query ? "Nothing matches that search." : "No warehouses loaded yet."}</p>}
            </div>
          </section>
        ))}

        {tab === "People" && <section className="card"><TeamPanel me={account} /></section>}
      </main>
    </AppShell>
  );
}
