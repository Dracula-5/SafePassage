"use client";

// The driver's trip, as one hook. Real GPS goes up every 5 seconds through a queue that survives dead zones,
// the route comes back on the same beat, and instructions can be read aloud in the driver's language. It
// authenticates with the device token, so it keeps working if the session expires mid-trip. A simulated truck
// has no phone behind it: it only follows its route and sends no GPS. A duty officer can watch any truck this way.

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, Assignment, DeviceRegistration, driverApi, DriverRequest } from "@/lib/api";
import { add, afterSend, nextBatch } from "@/lib/queue.mjs";

import { REFRESH_MS } from "../LiveStatus";

export const DEVICE = "rr_device";
export const QUEUE = "rr_queue";
const OFFLINE_FIX_MS = 30_000; // in a dead zone, fewer fixes make the phone's storage last 16 hours
const SPEECH_LANG: Record<string, string> = { te: "te-IN", hi: "hi-IN", en: "en-IN" };

type Fix = { lon: number; lat: number; recorded_at: string; speed_mps: number | null; heading_deg: number | null; accuracy_m: number };

export function read<T>(key: string, fallback: T): T {
  try {
    return JSON.parse(localStorage.getItem(key) ?? "null") ?? fallback;
  } catch {
    return fallback;
  }
}

export function write(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Storage full or blocked: the live fixes still go up.
  }
}

export function stopVerb(stop: string, route: string): string {
  if (route === "evacuation") return stop === "pickup" ? "Pick up people" : "Take to safety";
  return stop === "pickup" ? "Pick up" : "Deliver";
}

export function useTrip(device: DeviceRegistration, watching: boolean) {
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

  return {
    vehicle, noPhone, watching, assignment, request, routeChanged, gps, gpsFailed, online, queued, sentAt, revoked, speak, answer,
    dismissRouteChange: () => setRouteChanged(false),
  };
}

export type Trip = ReturnType<typeof useTrip>;
