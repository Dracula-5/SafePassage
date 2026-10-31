"use client";

// Driver's screen, made for a phone in a cab. Real GPS goes up every 5 seconds through a queue that survives
// dead zones, the route comes back on the same beat, and instructions can be read aloud in the driver's
// language. It authenticates with the device token, so it keeps working if the session expires mid-trip.

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { ApiError, Assignment, DeviceRegistration, driverApi } from "@/lib/api";
import { add, afterSend, nextBatch } from "@/lib/queue.mjs";
import { useAccount } from "@/lib/session";

import AppShell, { Loading } from "./AppShell";
import { REFRESH_MS } from "./LiveStatus";

const DEVICE = "rr_device";
const QUEUE = "rr_queue";
const OFFLINE_FIX_MS = 30_000; // in a dead zone, fewer fixes make the phone's storage last 16 hours
const SPEECH_LANG: Record<string, string> = { te: "te-IN", hi: "hi-IN", en: "en-IN" };

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

  useEffect(() => {
    if (!account) return;
    const saved = read<DeviceRegistration | null>(DEVICE, null);
    const adopt = (d: DeviceRegistration) => {
      write(DEVICE, d);
      setDevice(d);
    };
    driverApi.vehicle().then(
      (vehicle) => {
        if (saved?.vehicle.id === vehicle.id) setDevice({ ...saved, vehicle });
        else driverApi.useThisDevice().then(adopt, (err) => setBlocked(err.message));
      },
      (err) => {
        if (err instanceof ApiError && err.status === 404) setNeedsVehicle(true);
        else if (saved) setDevice(saved); // no signal: carry on with what this phone already knows
        else setBlocked("Connect to the internet once to set up this phone.");
      },
    );
  }, [account]);

  if (!account) return <Loading />;
  return (
    <AppShell account={account} title="Driver">
      {blocked ? <main className="phone"><div className="alert danger">{blocked}</div></main>
        : needsVehicle && !device ? <RegisterVehicle onDone={(d) => { write(DEVICE, d); setDevice(d); }} />
        : device ? <Trip device={device} />
        : <Loading label="Setting up this phone…" />}
    </AppShell>
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

function Trip({ device }: { device: DeviceRegistration }) {
  const { vehicle, device_token: token } = device;
  const [assignment, setAssignment] = useState<Assignment | null>(null);
  const [routeChanged, setRouteChanged] = useState(false);
  const [gps, setGps] = useState("Waiting for GPS…");
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
  }, [flush, loadAssignment]);

  if (revoked) {
    return <main className="phone"><div className="alert danger">This phone was removed from the fleet. Call your dispatcher.</div></main>;
  }
  const route = assignment?.route;
  const done = route?.completed_stops ?? 0;
  const next = route?.stops[done];
  return (
    <main className="phone">
      <section className="truck-bar">
        <div><small>Vehicle</small><b>{vehicle.label}</b></div>
        <div><small>GPS</small><b className={gpsFailed ? "bad" : "good"}>{gps}</b></div>
        <div><small>Network</small><b className={online ? "good" : "bad"}>{online ? "Online" : "No signal"}</b></div>
      </section>
      {!online && <div className="alert warn">No signal. Your position is being saved ({queued} waiting) and sends by itself when you are back in range.</div>}
      {routeChanged && <div className="alert route-alert" role="alert">New route. Follow the stops below.</div>}
      {assignment?.is_drill && <div className="drill-banner">DRILL. This is not a real dispatch.</div>}

      {!route ? (
        <section className="card waiting">
          <span className="pulse" aria-hidden="true" />
          <h1>No route yet</h1>
          <p className="muted">Keep this screen open. Your route appears here as soon as a duty officer approves it.</p>
          <small className="muted">{sentAt ? `Position last sent at ${sentAt}` : "Waiting to send your first position"}</small>
        </section>
      ) : (
        <>
          {next ? (
            <section className="card next-stop">
              <small>Next stop · {next.kind === "pickup" ? "Pick up" : "Deliver"}</small>
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
                  <b>{s.kind === "pickup" ? "Pick up" : "Deliver"}</b> {s.name}
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
    </main>
  );
}
