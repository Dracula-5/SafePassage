"use client";

// Driver's screen, made for a phone in a cab. Real GPS goes up every 5 seconds through a queue that survives
// dead zones, the route comes back on the same beat, and instructions can be read aloud in the driver's
// language. It authenticates with the device token, so it keeps working if the session expires mid-trip.
// A simulated truck has no phone behind it: the same screen only follows its route, and sends no GPS. A
// duty officer can open that view of any truck, to see what its driver sees.

import { useSearchParams } from "next/navigation";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { ApiError, Assignment, demoApi, DemoStatus, DeviceRegistration, driverApi, DriverRequest, plansApi, Vehicle } from "@/lib/api";
import { plate } from "@/lib/format";
import { add, afterSend, nextBatch } from "@/lib/queue.mjs";
import { useAccount } from "@/lib/session";

import AppShell, { Loading } from "./AppShell";
import { REFRESH_MS } from "./LiveStatus";

const DEVICE = "rr_device";
const QUEUE = "rr_queue";
const OFFLINE_FIX_MS = 30_000; // in a dead zone, fewer fixes make the phone's storage last 16 hours
const FLEET_TRUCK_KG = 8000; // orders are cut to fit the fleet, so a much smaller truck can't carry a part of one
const SPEECH_LANG: Record<string, string> = { te: "te-IN", hi: "hi-IN", en: "en-IN" };

function stopVerb(stop: string, route: string): string {
  if (route === "evacuation") return stop === "pickup" ? "Pick up people" : "Take to safety";
  return stop === "pickup" ? "Pick up" : "Deliver";
}

type Fix = { lon: number; lat: number; recorded_at: string; speed_mps: number | null; heading_deg: number | null; accuracy_m: number };

function read<T>(key: string, fallback: T): T {
  try {
    return JSON.parse(localStorage.getItem(key) ?? "null") ?? fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Storage full or blocked: the live fixes still go up.
  }
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
  return (
    <AppShell account={account} title="Driver">
      {blocked ? <main className="phone"><div className="alert danger">{blocked}</div></main>
        : needsVehicle && !device ? <RegisterVehicle onDone={(d) => { write(DEVICE, d); setDevice(d); }} />
        : device ? <Trip key={String(device.vehicle.simulated)} device={device} demo={demo} onSimulated={simulate} />
        : <Loading label="Setting up this phone…" />}
    </AppShell>
  );
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
      {vehicle ? <Trip device={{ vehicle, device_token: "" }} watching />
        : missing ? <main className="phone"><div className="alert danger">That truck is not on the fleet.</div></main>
        : <Loading />}
    </AppShell>
  );
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
    <details className="card">
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

  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      onDone(await driverApi.register({ label: label.trim(), driver_phone: phone.trim() || undefined, driver_language: language, capacity_kg: Number(capacity) }));
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <main className="phone">
      <section className="card">
        <h1>Register your vehicle</h1>
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
          <button className="primary">Register and start</button>
        </form>
      </section>
    </main>
  );
}

/** A duty officer asking this driver to go somewhere. Only the driver answers: a watching officer just sees it. */
function RequestCard({ request, canAnswer, onAnswer }: { request: DriverRequest; canAnswer: boolean; onAnswer: (accept: boolean) => void }) {
  return (
    <section className="card request" role="alert">
      <small>Request from {request.requested_by}</small>
      <h1>{request.place}</h1>
      {request.status === "sent" ? (
        <>
          <p>Please go to the edge of this area and wait there. Stay out of the marked zone.</p>
          {canAnswer ? (
            <div className="row">
              <button className="primary" onClick={() => onAnswer(true)}>Accept</button>
              <button onClick={() => onAnswer(false)}>Decline</button>
            </div>
          ) : <p className="muted small">Waiting for the driver to answer.</p>}
        </>
      ) : <p>Accepted. Wait at the edge of this area. Your route appears here once a plan is approved.</p>}
    </section>
  );
}

function Trip({ device, demo, onSimulated, watching = false }: {
  device: DeviceRegistration;
  demo?: DemoStatus | null;
  onSimulated?: (vehicle: Vehicle) => void;
  watching?: boolean;
}) {
  const { vehicle, device_token: token } = device;
  const noPhone = vehicle.simulated || watching; // nothing on this screen reports a position
  const [assignment, setAssignment] = useState<Assignment | null>(null);
  const [request, setRequest] = useState<DriverRequest | null>(null);
  const [routeChanged, setRouteChanged] = useState(false);
  const [gps, setGps] = useState(vehicle.simulated ? "Simulated" : noPhone ? "Not this phone" : "Waiting for GPS…");
  const [gpsFailed, setGpsFailed] = useState(false);
  const [online, setOnline] = useState(true);
  const [queued, setQueued] = useState(0);
  const [sentAt, setSentAt] = useState<string | null>(null);
  const [revoked, setRevoked] = useState(false);
  const lastFix = useRef(0);
  const routeId = useRef<string | null>(null);

  const speak = useCallback((text: string, language: string) => {
    if (!("speechSynthesis" in window)) return;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = SPEECH_LANG[language] ?? language;
    const voice = speechSynthesis.getVoices().find((v) => v.lang === utterance.lang);
    if (voice) utterance.voice = voice;
    speechSynthesis.cancel();
    speechSynthesis.speak(utterance);
  }, []);

  const flush = useCallback(async () => {
    const queue = read<Fix[]>(QUEUE, []);
    const batch = nextBatch(queue);
    if (!batch.length || !navigator.onLine) return;
    try {
      await driverApi.telemetry(vehicle.id, token, batch);
      write(QUEUE, afterSend(read<Fix[]>(QUEUE, []), batch, 202)); // resends are safe: the server ignores duplicates
      setSentAt(new Date().toLocaleTimeString());
    } catch (err) {
      const status = err instanceof ApiError ? err.status : null;
      write(QUEUE, afterSend(read<Fix[]>(QUEUE, []), batch, status));
      if (status === 401) setRevoked(true);
    }
    setQueued(read<Fix[]>(QUEUE, []).length);
  }, [vehicle.id, token]);

  const loadAssignment = useCallback(async () => {
    if (!navigator.onLine) return;
    driverApi.request(vehicle.id, token).then(setRequest, () => undefined);
    try {
      const next = await driverApi.assignment(vehicle.id, token);
      const changed = routeId.current !== null && routeId.current !== next.route.id; // the Replanner sent a replacement
      routeId.current = next.route.id;
      setAssignment(next);
      if (changed) {
        setRouteChanged(true);
        speak(next.instructions.text, next.instructions.language);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        routeId.current = null;
        setAssignment(null);
      }
      if (err instanceof ApiError && err.status === 401) setRevoked(true);
    }
  }, [vehicle.id, token, speak]);

  useEffect(() => {
    if (noPhone) {
      const first = setTimeout(loadAssignment, 0);
      const timer = setInterval(loadAssignment, REFRESH_MS);
      return () => {
        clearTimeout(first);
        clearInterval(timer);
      };
    }
    const syncOnline = () => {
      setOnline(navigator.onLine);
      if (navigator.onLine) {
        flush();
        loadAssignment();
      }
    };
    const first = setTimeout(() => {
      syncOnline();
      setQueued(read<Fix[]>(QUEUE, []).length);
    }, 0);
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js", { scope: "/driver" });
    const record = (p: GeolocationPosition) => {
      setGps(`±${Math.round(p.coords.accuracy)} m`);
      setGpsFailed(false);
      const now = Date.now();
      if (now - lastFix.current < (navigator.onLine ? REFRESH_MS : OFFLINE_FIX_MS)) return;
      lastFix.current = now;
      const fix: Fix = { lon: p.coords.longitude, lat: p.coords.latitude, recorded_at: new Date(p.timestamp).toISOString(), speed_mps: p.coords.speed ?? null,
        heading_deg: p.coords.heading != null && p.coords.heading < 360 ? p.coords.heading : null, accuracy_m: p.coords.accuracy };
      const queue = add(read<Fix[]>(QUEUE, []), fix);
      write(QUEUE, queue);
      setQueued(queue.length);
    };
    const hasGps = "geolocation" in navigator;
    // A phone that sleeps stops sending GPS, and a browser cannot do that in the background.
    let awake: WakeLockSentinel | null = null;
    const stayAwake = () => {
      if (document.visibilityState === "visible" && "wakeLock" in navigator) {
        navigator.wakeLock.request("screen").then((lock) => { awake = lock; }, () => undefined);
      }
    };
    stayAwake();
    document.addEventListener("visibilitychange", stayAwake);
    const watch = hasGps ? navigator.geolocation.watchPosition(record, (e) => {
      setGps(e.message);
      setGpsFailed(true);
    }, { enableHighAccuracy: true, maximumAge: 2000, timeout: 30000 }) : null;
    const timer = setInterval(() => {
      // A parked truck's GPS can go quiet, and silence reads as offline, so ask for a fresh reading.
      if (hasGps) navigator.geolocation.getCurrentPosition(record, () => undefined, { enableHighAccuracy: true, maximumAge: 2000, timeout: REFRESH_MS - 500 });
      flush();
      loadAssignment();
    }, REFRESH_MS);
    window.addEventListener("online", syncOnline);
    window.addEventListener("offline", syncOnline);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
      if (watch !== null) navigator.geolocation.clearWatch(watch);
      window.removeEventListener("online", syncOnline);
      window.removeEventListener("offline", syncOnline);
      document.removeEventListener("visibilitychange", stayAwake);
      awake?.release();
    };
  }, [flush, loadAssignment, noPhone]);

  async function answer(accept: boolean) {
    await driverApi.answer(vehicle.id, token, accept).catch(() => undefined); // a stale screen: the next refresh corrects it
    loadAssignment();
  }

  if (revoked) {
    return <main className="phone"><div className="alert danger">This phone was removed from the fleet. Call your dispatcher.</div></main>;
  }
  const route = assignment?.route;
  const done = route?.completed_stops ?? 0;
  const next = route?.stops[done];
  return (
    <main className="phone">
      <section className="truck-bar">
        <div><small>Truck {vehicle.fleet_number}</small><b>{plate(vehicle.label)}</b></div>
        <div><small>GPS</small><b className={gpsFailed ? "bad" : "good"}>{gps}</b></div>
        <div><small>Network</small><b className={online ? "good" : "bad"}>{online ? "Online" : "No signal"}</b></div>
      </section>
      {vehicle.simulated && <div className="alert warn"><span className="badge sim">SIMULATED</span> This truck and its GPS are simulated for a presentation. No real vehicle is moving.</div>}
      {!online && <div className="alert warn">No signal. Your position is being saved ({queued} waiting) and sends by itself when you are back in range.</div>}
      {routeChanged && <div className="alert route-alert" role="alert">New route. Follow the stops below.</div>}
      {assignment?.is_drill && <div className="drill-banner">DRILL. This is not a real dispatch.</div>}
      {route?.kind === "evacuation" && <div className="alert ok"><b>Evacuation.</b> You are carrying people. The way out avoids the active hazard zones.</div>}
      {request && !route && <RequestCard request={request} canAnswer={!watching} onAnswer={answer} />}

      {!route ? (
        <section className="card waiting">
          <span className="pulse" aria-hidden="true" />
          <h1>No route yet</h1>
          <p className="muted">Keep this screen open. Your route appears here as soon as a duty officer approves it.</p>
          {!noPhone && <small className="muted">{sentAt ? `Position last sent at ${sentAt}` : "Waiting to send your first position"}</small>}
        </section>
      ) : (
        <>
          {next ? (
            <section className="card next-stop">
              <small>Next stop · {stopVerb(next.kind, route.kind)}</small>
              <h1>{next.name}</h1>
              <p>By {new Date(next.eta).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</p>
            </section>
          ) : <section className="card next-stop done"><h1>Route complete</h1><p>Thank you.</p></section>}

          <section className="card">
            <div className="row-between">
              <b>{done} of {route.stops.length} stops</b>
              <span className="muted">{(route.distance_m / 1000).toFixed(0)} km · {(route.duration_s / 3600).toFixed(1)} h</span>
            </div>
            <div className="progress" aria-hidden="true"><span style={{ width: `${(100 * done) / route.stops.length}%` }} /></div>
            <ol className="stops">
              {route.stops.map((s, i) => (
                <li key={`${s.facility_id}-${i}`} className={i < done ? "done" : i === done ? "current" : ""}>
                  <b>{stopVerb(s.kind, route.kind)}</b> {s.name}
                  <small>by {new Date(s.eta).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</small>
                </li>
              ))}
            </ol>
            <p className="safe-note">This route avoids active hazard zones. Do not take shortcuts.</p>
          </section>

          <section className="card">
            <div className="row-between"><b>Instructions</b>
              {assignment.instructions.text && <button className="primary" onClick={() => speak(assignment.instructions.text, assignment.instructions.language)}>🔊 Read aloud</button>}
            </div>
            {assignment.instructions.text
              ? <p className="instructions">{assignment.instructions.text}</p>
              : <p className="muted">Preparing your instructions in your language. The stops above are ready now.</p>}
          </section>
        </>
      )}
      {demo?.enabled && !noPhone && onSimulated && <SimulateGps demo={demo} capacity={vehicle.capacity_kg} onDone={onSimulated} />}
    </main>
  );
}
