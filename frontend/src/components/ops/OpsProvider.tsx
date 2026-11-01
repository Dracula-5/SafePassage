"use client";

// One source of live operational data for every duty-officer page. It polls the small status calls every
// 5 seconds, reloads the large hazard shapes only when the server says they changed, and keeps one live
// stream open for plan, truck and reasoning events. Pages read it with useOps() and never fetch on their own,
// so moving between pages costs nothing and nothing shows a number that another page contradicts.

import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import {
  Account, AgentEvent, api, demoApi, DemoStatus, DriverRequest, Evacuation, FeatureCollection, HazardProperties, LiveRoute, Overview, plansApi, PlanSummary,
  PublicStatus, regionBbox, requestsApi, SourceHealth, STREAM_URL, Vehicle,
} from "@/lib/api";

import { REFRESH_MS } from "../LiveStatus";

const RECONNECT_MS = 3000;
const MAX_EVENTS = 300;

export interface Ops {
  account: Account;
  status: PublicStatus | null;
  overview: Overview | null;
  hazards: FeatureCollection<HazardProperties> | null;
  health: SourceHealth[];
  vehicles: Vehicle[];
  demo: DemoStatus | null;
  requests: DriverRequest[];
  liveRoutes: LiveRoute[];
  plans: PlanSummary[];
  evacuations: Evacuation[];
  events: AgentEvent[];
  /** When the last full refresh succeeded, and whether the latest one failed. */
  updatedAt: number | null;
  failed: boolean;
  plansError: string | null;
  stream: "connecting" | "live" | "reconnecting";
  streamMessage: string | null;
  /** Goes up each time the plan list is read again, so an open plan knows to read itself again. */
  plansVersion: number;
  bounds: [number, number, number, number] | null;
  refresh: () => Promise<void>;
  refreshPlans: () => Promise<void>;
  reloadHazards: () => void;
}

const OpsContext = createContext<Ops | null>(null);

export function useOps(): Ops {
  const ops = useContext(OpsContext);
  if (!ops) throw new Error("useOps must be used inside OpsProvider");
  return ops;
}

export function OpsProvider({ account, children }: { account: Account; children: ReactNode }) {
  const [status, setStatus] = useState<PublicStatus | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [hazards, setHazards] = useState<FeatureCollection<HazardProperties> | null>(null);
  const [health, setHealth] = useState<SourceHealth[]>([]);
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [demo, setDemo] = useState<DemoStatus | null>(null);
  const [requests, setRequests] = useState<DriverRequest[]>([]);
  const [liveRoutes, setLiveRoutes] = useState<LiveRoute[]>([]);
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [evacuations, setEvacuations] = useState<Evacuation[]>([]);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);
  const [plansError, setPlansError] = useState<string | null>(null);
  const [plansVersion, setPlansVersion] = useState(0);
  const [stream, setStream] = useState<Ops["stream"]>("connecting");
  const [streamMessage, setStreamMessage] = useState<string | null>(null);
  const bounds = useMemo(() => regionBbox(), []);
  const hazardStamp = useRef("");

  const refresh = useCallback(async () => {
    try {
      const [s, h, v, o, d, r, l] = await Promise.all([
        api.status(), api.sourceHealth(), plansApi.vehicles(), api.overview(), demoApi.status(), requestsApi.list(), plansApi.liveRoutes(),
      ]);
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

  const refreshPlans = useCallback(async () => {
    try {
      const [p, e] = await Promise.all([plansApi.list(), plansApi.evacuations()]);
      setPlans(p);
      setEvacuations(e);
      setPlansError(null);
      setPlansVersion((n) => n + 1);
    } catch (err) {
      setPlansError((err as Error).message);
    }
  }, []);

  const reloadHazards = useCallback(() => {
    hazardStamp.current = "";
    refresh();
  }, [refresh]);

  useEffect(() => {
    const first = setTimeout(() => { refresh(); refreshPlans(); }, 0);
    const timer = setInterval(() => { if (!document.hidden) { refresh(); refreshPlans(); } }, REFRESH_MS);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [refresh, refreshPlans]);

  // The live stream (SSE) resumes by itself with Last-Event-ID. When it drops, anything could have changed.
  useEffect(() => {
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let soon: ReturnType<typeof setTimeout> | null = null;
    let closed = false;
    let lost = false;
    const refreshSoon = () => {
      if (soon) clearTimeout(soon);
      soon = setTimeout(refreshPlans, 500);
    };
    const connect = () => {
      if (closed) return;
      const s = new EventSource(STREAM_URL);
      source = s;
      s.onopen = () => {
        setStream("live");
        setStreamMessage(null);
        if (lost) {
          lost = false;
          refreshPlans();
          reloadHazards();
        }
      };
      s.onerror = () => {
        lost = true;
        setStream("reconnecting");
        if (s.readyState === EventSource.CLOSED) retry = setTimeout(connect, RECONNECT_MS);
      };
      // The server sends its own `error` event when Postgres or Redis hiccups; the stream stays open.
      s.addEventListener("error", (e) => {
        const data = (e as MessageEvent).data;
        if (data) setStreamMessage((JSON.parse(data) as { message: string }).message);
      });
      s.addEventListener("plan", refreshSoon);
      s.addEventListener("agent_event", (e) => {
        const ev = JSON.parse((e as MessageEvent).data) as AgentEvent;
        setEvents((prev) => [ev, ...prev.filter((p) => p.id !== ev.id)].slice(0, MAX_EVENTS));
        refreshSoon();
      });
      s.addEventListener("vehicle", (e) => {
        const v = JSON.parse((e as MessageEvent).data) as Vehicle;
        setVehicles((prev) => [...prev.filter((p) => p.id !== v.id), v]);
      });
      s.addEventListener("hazard", reloadHazards);
    };
    connect();
    return () => {
      closed = true;
      if (soon) clearTimeout(soon);
      if (retry) clearTimeout(retry);
      source?.close();
    };
  }, [refreshPlans, reloadHazards]);

  const value: Ops = {
    account, status, overview, hazards, health, vehicles, demo, requests, liveRoutes, plans, evacuations, events, updatedAt, failed, plansError,
    stream, streamMessage, plansVersion, bounds, refresh, refreshPlans, reloadHazards,
  };
  return <OpsContext.Provider value={value}>{children}</OpsContext.Provider>;
}
