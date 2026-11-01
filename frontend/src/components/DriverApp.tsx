"use client";

// Driver's app, made for a phone in a cab. This file decides what to show (setting up, registering a vehicle, or the
// trip); the trip itself is the useTrip hook and the six screens are in ./driver. It authenticates with the device
// token, so it keeps working if the session expires mid-trip. A duty officer can open the read-only view of any truck.

import { useSearchParams } from "next/navigation";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { Account, ApiError, demoApi, DemoStatus, DeviceRegistration, driverApi, plansApi, Vehicle } from "@/lib/api";
import { plate } from "@/lib/format";
import { useAccount } from "@/lib/session";

import AppShell, { Loading } from "./AppShell";
import styles from "./driver/driver.module.css";
import { AssignmentsScreen, DriverNav, HomeScreen, parseDriverView, ProfileScreen, RouteScreen, StatusScreen, StopsScreen } from "./driver/DriverScreens";
import { DEVICE, QUEUE, read, useTrip, write } from "./driver/useTrip";
import { useHash } from "./supplier/nav";
import { BrandMark } from "./ui";

const FLEET_TRUCK_KG = 8000; // orders are cut to fit the fleet, so a much smaller truck can't carry a part of one

function Frame({ chip, children }: { chip?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className={styles.app}>
      <header className={styles.header}>
        <span className={styles.brand}><BrandMark size={30} /> SafePassage</span>
        <span className="spacer" />
        {chip}
      </header>
      {children}
    </div>
  );
}

export default function DriverApp() {
  const account = useAccount("driver");
  const [device, setDevice] = useState<DeviceRegistration | null>(null);
  const [needsVehicle, setNeedsVehicle] = useState(false);
  const [blocked, setBlocked] = useState<string | null>(null);
  const [demo, setDemo] = useState<DemoStatus | null>(null);

  const simulate = useCallback((vehicle: Vehicle) => {
    write(DEVICE, null);
    write(QUEUE, []);
    setDevice({ vehicle, device_token: "" });
  }, []);

  useEffect(() => {
    if (!account) return;
    demoApi.status().then(setDemo, () => undefined);
    const saved = read<DeviceRegistration | null>(DEVICE, null);
    const adopt = (d: DeviceRegistration) => {
      write(DEVICE, d);
      setDevice(d);
    };
    driverApi.vehicle().then(
      (vehicle) => {
        if (vehicle.simulated) simulate(vehicle);
        else if (saved?.vehicle.id === vehicle.id) setDevice({ ...saved, vehicle });
        else driverApi.useThisDevice().then(adopt, (err) => setBlocked(err.message));
      },
      (err) => {
        if (err instanceof ApiError && err.status === 404) setNeedsVehicle(true);
        else if (saved) setDevice(saved); // no signal: carry on with what this phone already knows
        else setBlocked("Connect to the internet once to set up this phone.");
      },
    );
  }, [account, simulate]);

  if (!account) return <Loading />;
  if (blocked) return <Frame><main className={styles.wrap}><div className={`${styles.notice} ${styles.nBad}`} role="alert">{blocked}</div></main></Frame>;
  if (needsVehicle && !device) return <Frame><RegisterVehicle onDone={(d) => { write(DEVICE, d); setDevice(d); }} /></Frame>;
  if (!device) return <Frame><Loading label="Setting up this phone…" /></Frame>;
  return <Trip key={String(device.vehicle.simulated)} device={device} demo={demo} account={account} onSimulated={simulate} />;
}

/** What a driver sees, for a duty officer. It changes nothing: no GPS is sent from here. */
export function DriverView() {
  const account = useAccount("operator");
  const id = useSearchParams().get("vehicle");
  const [vehicle, setVehicle] = useState<Vehicle | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    if (!account) return;
    plansApi.vehicles().then((list) => {
      const found = list.find((v) => v.id === id) ?? null;
      setVehicle(found);
      setMissing(!found);
    }, () => setMissing(true));
  }, [account, id]);

  if (!account) return <Loading />;
  return (
    <AppShell account={account} title={vehicle ? `${vehicle.driver_name ?? plate(vehicle.label)}'s screen` : "Driver's screen"}>
      {vehicle ? <Trip device={{ vehicle, device_token: "" }} account={null} watching />
        : missing ? <main className={styles.wrap}><div className={`${styles.notice} ${styles.nBad}`} role="alert">That truck is not on the fleet.</div></main>
        : <Loading />}
    </AppShell>
  );
}

function Trip({ device, demo, account, onSimulated, watching = false }: {
  device: DeviceRegistration;
  demo?: DemoStatus | null;
  account: Account | null;
  onSimulated?: (vehicle: Vehicle) => void;
  watching?: boolean;
}) {
  const trip = useTrip(device, watching);
  const view = parseDriverView(useHash());
  const body = trip.revoked ? (
    <main className={styles.wrap}><div className={`${styles.notice} ${styles.nBad}`} role="alert">This phone was removed from the fleet. Call your dispatcher.</div></main>
  ) : (
    <>
      <main className={`${styles.wrap} ${watching ? styles.wrapWatching : ""}`}>
        {view === "home" && <HomeScreen trip={trip} account={account} />}
        {view === "assignments" && <AssignmentsScreen trip={trip} />}
        {view === "route" && <RouteScreen trip={trip} />}
        {view === "stops" && <StopsScreen trip={trip} />}
        {view === "status" && <StatusScreen trip={trip} />}
        {view === "profile" && (
          <ProfileScreen trip={trip} account={account}
            extra={demo?.enabled && !trip.noPhone && onSimulated ? <SimulateGps demo={demo} capacity={trip.vehicle.capacity_kg} onDone={onSimulated} /> : undefined} />
        )}
      </main>
      <DriverNav view={view} trip={trip} />
    </>
  );
  if (watching) return <div className={styles.app}>{body}</div>;
  const bad = !trip.online || trip.gpsFailed;
  return <Frame chip={<span className={`${styles.chip} ${bad ? styles.chipBad : ""}`}><i />{trip.online ? (trip.gpsFailed ? "GPS problem" : "Online") : "No signal"}</span>}>{body}</Frame>;
}

/** A phone outside Andhra Pradesh can't be routed: the road map stops at the state's border. */
function SimulateGps({ demo, capacity, onDone }: { demo: DemoStatus; capacity: number; onDone: (v: Vehicle) => void }) {
  const [town, setTown] = useState(demo.towns?.[0]?.name ?? "");
  const [load, setLoad] = useState(String(Math.max(capacity, FLEET_TRUCK_KG)));
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      onDone(await demoApi.simulateMyTruck({ town, capacity_kg: Number(load) }));
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <details className={styles.card}>
      <summary><b>Not in Andhra Pradesh? Simulate this truck&apos;s GPS</b></summary>
      <p className="muted small">For a presentation. Your truck appears in the town you choose and drives its routes by itself. It stays marked as simulated, and this phone stops sending its position.</p>
      <form className="form" onSubmit={submit}>
        <label>Start from
          <select value={town} onChange={(e) => setTown(e.target.value)}>
            {demo.towns?.map((t) => <option key={t.name}>{t.name}</option>)}
          </select>
        </label>
        <label>Load capacity (kg)<input required type="number" min={1} max={60000} inputMode="numeric" value={load} onChange={(e) => setLoad(e.target.value)} /></label>
        {error && <div className="alert danger" role="alert">{error}</div>}
        <button className="primary">Simulate my GPS</button>
      </form>
    </details>
  );
}

function RegisterVehicle({ onDone }: { onDone: (d: DeviceRegistration) => void }) {
  const [label, setLabel] = useState("");
  const [phone, setPhone] = useState("");
  const [language, setLanguage] = useState("te");
  const [capacity, setCapacity] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      onDone(await driverApi.register({ label: label.trim(), driver_phone: phone.trim() || undefined, driver_language: language, capacity_kg: Number(capacity) }));
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <main className={styles.wrap}>
      <section className={styles.card}>
        <h1 className={styles.title}>Register your vehicle</h1>
        <p className="muted">Once this is done, your truck appears on the duty officer&apos;s map.</p>
        <form className="form" onSubmit={submit}>
          <label>Vehicle number<input required maxLength={100} placeholder="AP 16 TX 1234" value={label} onChange={(e) => setLabel(e.target.value)} /></label>
          <label>Load capacity (kg)<input required type="number" min={1} max={60000} inputMode="numeric" value={capacity} onChange={(e) => setCapacity(e.target.value)} /></label>
          <label>Phone (optional)<input inputMode="tel" placeholder="+9198xxxxxxxx" value={phone} onChange={(e) => setPhone(e.target.value)} /></label>
          <label>Instructions in
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              <option value="te">తెలుగు (Telugu)</option>
              <option value="hi">हिन्दी (Hindi)</option>
              <option value="en">English</option>
            </select>
          </label>
          {error && <div className="alert danger" role="alert">{error}</div>}
          <button className={`primary ${styles.big}`} disabled={busy}>{busy ? "Registering…" : "Register and start"}</button>
        </form>
      </section>
    </main>
  );
}
