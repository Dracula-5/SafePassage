"use client";

// Everyone and everything the system knows about on the ground: suppliers and what they can give now, every truck
// and its driver, and any facility by name. Most suppliers come from AePDS, so they have no account to appear under.

import { useCallback, useEffect, useMemo, useState } from "react";

import { api, FacilityMatch, FacilityProperties, SupplierListing, supplierApi, teamApi, Vehicle } from "@/lib/api";
import { ago, itemName, plate, tonnes } from "@/lib/format";

import { REFRESH_MS } from "../LiveStatus";
import { AlertCard, Column, ConfirmDialog, DataTable, EmptyState, FilterBar, FilterChips, PageHeader, SearchInput, StatusBadge, Surface } from "../ui";
import { matches, words } from "./constants";
import styles from "./ops.module.css";
import { useOps } from "./OpsProvider";

const MAX_ROWS = 100; // a search narrows the rest; a table of hundreds helps nobody
const SOURCE_NAMES: Record<string, string> = { osm: "OpenStreetMap", datagov: "data.gov.in", portal: "Registered here" };
type Tab = "suppliers" | "drivers" | "warehouses" | "places";

export default function DirectoryPage() {
  const { vehicles } = useOps();
  const [tab, setTab] = useState<Tab>("suppliers");
  const [query, setQuery] = useState("");
  const [suppliers, setSuppliers] = useState<SupplierListing[] | null>(null);
  const [warehouses, setWarehouses] = useState<FacilityProperties[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [withdrawing, setWithdrawing] = useState<SupplierListing | null>(null);
  const [busy, setBusy] = useState(false);
  const [found, setFound] = useState<{ text: string; matches: FacilityMatch[] | null } | null>(null);

  const load = useCallback(() => teamApi.suppliers().then((s) => { setSuppliers(s); setError(null); }, (err) => setError(err.message)), []);

  useEffect(() => {
    const first = setTimeout(load, 0);
    const timer = setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
    return () => { clearTimeout(first); clearInterval(timer); };
  }, [load]);

  useEffect(() => {
    if (tab !== "warehouses" || warehouses !== null) return;
    api.warehouses().then(
      (collection) => setWarehouses(collection.features.map((feature) => feature.properties)),
      (err) => setError((err as Error).message),
    );
  }, [tab, warehouses]);

  // A place search answers the text it was sent for, so an older reply is never shown for newer typing.
  const text = query.trim();
  useEffect(() => {
    if (tab !== "places" || text.length < 2) return;
    let current = true;
    const timer = setTimeout(() => {
      supplierApi.search(text, null).then(
        (m) => { if (current) setFound({ text, matches: m }); },
        () => { if (current) setFound({ text, matches: null }); },
      );
    }, 300);
    return () => { current = false; clearTimeout(timer); };
  }, [tab, text]);

  const trucks = useMemo(() => vehicles
    .filter((v) => v.status !== "maintenance" && matches(query, v.label, v.driver_name, String(v.fleet_number)))
    .sort((a, b) => Number(a.simulated) - Number(b.simulated) || Number(b.on_route) - Number(a.on_route) || plate(a.label).localeCompare(plate(b.label))), [vehicles, query]);
  const shops = useMemo(() => (suppliers ?? []).filter((s) => matches(query, s.name, s.site, s.contact)), [suppliers, query]);
  const stores = useMemo(() => (warehouses ?? [])
    .filter((w) => matches(query, w.name, w.kind))
    .sort((a, b) => Number(b.is_supplier) - Number(a.is_supplier) || a.name.localeCompare(b.name)), [warehouses, query]);
  const answer = found?.text === text ? found : null;

  async function setVerified(s: SupplierListing, verified: boolean) {
    setBusy(true);
    try {
      await teamApi.verifySupplier(s.id, verified);
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
      setWithdrawing(null);
    }
  }

  const supplierColumns: Column<SupplierListing>[] = [
    { key: "name", header: "Supplier", render: (s) => <span className={styles.cell}><b>{s.name}{s.name.startsWith("TEST-SIM") && <span className="badge sim">SIMULATED</span>}</b>{s.site !== s.name && <small>{s.site}</small>}{s.contact && <small>{s.contact}</small>}</span> },
    { key: "stock", header: "Can give now", render: (s) => s.stock.filter((i) => i.available > 0).map((i) => `${Math.round(i.available).toLocaleString()} ${itemName(i.item_code)}`).join(", ") || <span className={styles.muted}>Nothing available</span> },
    { key: "reported", header: "Reported", render: (s) => <span className="small">{ago(s.reported_at)}</span> },
    { key: "status", header: "Status", render: (s) => (
      <span className={styles.cell}>
        <span><StatusBadge tone={s.verified ? "good" : "warn"}>{s.verified ? "Verified" : "Unverified"}</StatusBadge></span>
        <button type="button" className="link" disabled={busy} onClick={() => (s.verified ? setWithdrawing(s) : setVerified(s, true))}>{s.verified ? "Withdraw" : "Verify"}</button>
      </span>
    ) },
  ];
  const truckColumns: Column<Vehicle>[] = [
    { key: "no", header: "No.", render: (v) => <span className="truck-no">{v.fleet_number}</span> },
    { key: "truck", header: "Truck", render: (v) => <b>{plate(v.label)}{v.simulated && <span className="badge sim">SIMULATED</span>}</b> },
    { key: "driver", header: "Driver", render: (v) => v.driver_name ?? <span className={styles.muted}>Not given</span> },
    { key: "cap", header: "Capacity", numeric: true, render: (v) => tonnes(v.capacity_kg) },
    { key: "status", header: "Status", render: (v) => <StatusBadge tone={v.is_offline ? "bad" : v.on_route ? "info" : "good"}>{v.is_offline ? "Offline" : v.on_route ? "On a route" : "Waiting"}</StatusBadge> },
    { key: "gps", header: "Last GPS", render: (v) => <span className="small">{ago(v.last_seen_at)}</span> },
  ];
  const warehouseColumns: Column<FacilityProperties>[] = [
    { key: "name", header: "Name", render: (w) => <b>{w.name}{w.name.startsWith("TEST-SIM") && <span className="badge sim">SIMULATED</span>}</b> },
    { key: "kind", header: "Type", render: (w) => <span className="capitalize">{w.kind}</span> },
    { key: "supplier", header: "Supplier here?", render: (w) => w.is_supplier ? <StatusBadge tone="good">Yes</StatusBadge> : <span className={styles.muted}>No</span> },
    { key: "source", header: "From", render: (w) => SOURCE_NAMES[w.source] ?? w.source },
  ];

  return (
    <>
      <PageHeader title="Directory" subtitle="Suppliers, drivers and places the system knows about." />
      {error && <AlertCard tone="bad" title="Could not load the directory">{error}</AlertCard>}
      <FilterBar>
        <FilterChips label="Directory section" value={tab} onChange={(t) => { setTab(t); setQuery(""); }} options={[
          { key: "suppliers", label: "Suppliers", count: suppliers?.length }, { key: "drivers", label: "Drivers", count: vehicles.filter((v) => v.status !== "maintenance").length }, { key: "warehouses", label: "Warehouses", count: warehouses?.length }, { key: "places", label: "Places" },
        ]} />
        <SearchInput value={query} onChange={setQuery} placeholder={tab === "drivers" ? "Search a driver, plate or truck number" : tab === "warehouses" ? "Search a warehouse or godown" : tab === "places" ? "Type at least 2 letters of a facility's name" : "Search a supplier or place"} />
      </FilterBar>

      {tab === "suppliers" && (suppliers === null && !error ? <Surface><p className={styles.muted}>Loading suppliers…</p></Surface> : (
        <Surface flush>
          {shops.length > MAX_ROWS && <p className={styles.muted} style={{ padding: "14px 20px 0", fontSize: 13 }}>Showing {MAX_ROWS} of {shops.length.toLocaleString()}. Search to find the rest.</p>}
          <DataTable caption="Suppliers" columns={supplierColumns} rows={shops.slice(0, MAX_ROWS)} rowKey={(s) => s.id} empty={<EmptyState icon="boxes" title={query ? "Nobody matches" : "No suppliers yet"}>{query ? "Try a different search." : "Suppliers appear here when they register or report stock."}</EmptyState>} />
        </Surface>
      ))}
      {tab === "drivers" && (
        <Surface flush>
          <DataTable caption="Drivers and trucks" columns={truckColumns} rows={trucks} rowKey={(v) => v.id} empty={<EmptyState icon="truck" title={query ? "Nobody matches" : "No trucks yet"} />} />
        </Surface>
      )}
      {tab === "warehouses" && (
        <Surface flush>
          {warehouses === null ? <p className={styles.muted} style={{ padding: 20 }}>Loading warehouses…</p> : (
            <>
              {stores.length > MAX_ROWS && <p className={styles.muted} style={{ padding: "14px 20px 0", fontSize: 13 }}>Showing {MAX_ROWS} of {stores.length.toLocaleString()}. Search to find the rest.</p>}
              <DataTable caption="Warehouses and godowns" columns={warehouseColumns} rows={stores.slice(0, MAX_ROWS)} rowKey={(w) => w.id} empty={<EmptyState icon="boxes" title={query ? "Nothing matches that search" : "No warehouses loaded yet"} />} />
            </>
          )}
        </Surface>
      )}
      {tab === "places" && (
        <Surface>
          {text.length < 2 ? <EmptyState icon="pin" title="Search for a place">Hospitals, shelters, warehouses and other facilities, by name.</EmptyState>
            : !answer ? <p className={styles.muted}>Searching…</p>
            : answer.matches === null ? <AlertCard tone="bad" title="Search is unavailable">Try again in a moment.</AlertCard>
            : answer.matches.length === 0 ? <EmptyState icon="pin" title="No matching places" />
            : <ul className={styles.stackTight}>{answer.matches.map((m) => <li key={m.id} className="row-between"><b>{m.name}</b><StatusBadge tone="plain">{words(m.kind)}</StatusBadge></li>)}</ul>}
        </Surface>
      )}

      <ConfirmDialog open={withdrawing !== null} danger title="Withdraw verification?" confirmLabel="Withdraw" busy={busy} onCancel={() => setWithdrawing(null)}
        onConfirm={() => withdrawing && setVerified(withdrawing, false)}>
        <p>{withdrawing?.name} becomes unverified, and its stock link stops working too.</p>
      </ConfirmDialog>
    </>
  );
}
