"use client";

// Right-hand operations panel: plans and the human approval gate, the live reasoning timeline, and the team.
// The timeline comes from GET /api/stream (SSE), which resumes with Last-Event-ID by itself.

import { useCallback, useEffect, useRef, useState } from "react";

import { Account, AgentEvent, Escalation, Evacuation, PlanDetail, plansApi, PlanSummary, STREAM_URL, Vehicle } from "@/lib/api";
import { ago, plate } from "@/lib/format";

import { REFRESH_MS } from "./LiveStatus";
import TeamPanel from "./TeamPanel";

const RECONNECT_MS = 3000;
const STATUS_TONES: Record<string, string> = {
  pending: "grey", planning: "blue", awaiting_approval: "orange", approved: "green", dispatched: "green",
  rejected: "red", failed: "red", unreachable: "red", superseded: "grey",
};
const EVENT_ICONS: Record<string, string> = {
  node_started: "▶", node_finished: "■", tool_call: "⚙", tool_result: "⇣", decision: "✔", critic_rejection: "✖",
  approval_requested: "⏸", approval_decided: "👤", dispatch: "🚚", data_stale: "⚠", error: "⚠",
};
const TABS = ["Plans", "Evacuations", "Activity", "Team"] as const;
const MAX_LINES = 3; // the point of a plan fits here: the rest is one click away
const EVACUATION_STAGES: Record<Evacuation["stage"], { text: string; tone: string }> = {
  waiting: { text: "Waiting for a truck", tone: "orange" }, on_the_way: { text: "Truck on the way", tone: "blue" },
  on_board: { text: "People on board", tone: "blue" }, safe: { text: "Reached safety", tone: "green" }, failed: { text: "Needs a person", tone: "red" },
};

/** The same shortfall at each relief point reads as noise: one line per item and reason, with the total. */
function escalationLines(escalations: Escalation[]): string[] {
  const totals = new Map<string, { quantity: number; item: string; reason: string; sites: number }>();
  const others: string[] = [];
  for (const e of escalations) {
    if (!("quantity_short" in e)) {
      others.push(e.reason);
      continue;
    }
    const key = `${e.item_code}|${e.reason}`;
    const line = totals.get(key) ?? { quantity: 0, item: e.item_code, reason: e.reason, sites: 0 };
    line.quantity += e.quantity_short;
    line.sites += 1;
    totals.set(key, line);
  }
  const grouped = [...totals.values()].map(
    (l) => `${Math.round(l.quantity).toLocaleString()} ${l.item}${l.sites > 1 ? ` across ${l.sites} sites` : ""}: ${l.reason}`,
  );
  return [...others, ...grouped];
}

interface Props {
  selectedPlanId: string | null;
  account: Account;
  vehicles: Vehicle[];
  onSelectPlan: (plan: PlanDetail | null) => void;
  onVehicle: (v: Vehicle) => void;
  onHazardChange: () => void;
}

export default function OpsPanel({ selectedPlanId, account, vehicles, onSelectPlan, onVehicle, onHazardChange }: Props) {
  const [tab, setTab] = useState<(typeof TABS)[number]>("Plans");
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [allRoutes, setAllRoutes] = useState(false);
  const [evacuations, setEvacuations] = useState<Evacuation[]>([]);
  const [detail, setDetail] = useState<PlanDetail | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [live, setLive] = useState<"connecting" | "live" | "reconnecting">("connecting");
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [degraded, setDegraded] = useState<string | null>(null);
  const selected = useRef<string | null>(selectedPlanId);
  const callbacks = useRef({ onSelectPlan, onVehicle, onHazardChange });
  useEffect(() => {
    selected.current = selectedPlanId;
    callbacks.current = { onSelectPlan, onVehicle, onHazardChange };
  });

  const refreshPlans = useCallback(async () => {
    try {
      setPlans(await plansApi.list());
      setEvacuations(await plansApi.evacuations());
      if (selected.current) {
        const d = await plansApi.get(selected.current);
        setDetail(d);
        callbacks.current.onSelectPlan(d);
      }
    } catch (err) {
      setMsg(`Plans unavailable: ${(err as Error).message}`);
    }
  }, []);

  useEffect(() => {
    const first = setTimeout(refreshPlans, 0);
    const poll = setInterval(() => { if (!document.hidden) refreshPlans(); }, REFRESH_MS);
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let closed = false;
    let pending: ReturnType<typeof setTimeout> | null = null;
    const refreshSoon = () => {
      if (pending) clearTimeout(pending);
      pending = setTimeout(refreshPlans, 500);
    };
    let lost = false;
    const connect = () => {
      if (closed) return;
      const stream = new EventSource(STREAM_URL);
      source = stream;
      stream.onopen = () => {
        setLive("live");
        setDegraded(null);
        if (lost) {  // anything could have changed while the stream was down
          lost = false;
          refreshPlans();
          callbacks.current.onHazardChange();
        }
      };
      stream.onerror = () => {
        lost = true;
        setLive("reconnecting");
        if (stream.readyState === EventSource.CLOSED) retry = setTimeout(connect, RECONNECT_MS);
      };
      // The server sends its own `error` event when Postgres or Redis hiccups; the stream stays open.
      stream.addEventListener("error", (e) => {
        const data = (e as MessageEvent).data;
        if (data) setDegraded((JSON.parse(data) as { message: string }).message);
      });
      stream.addEventListener("plan", refreshSoon);
      stream.addEventListener("agent_event", (e) => {
        const ev = JSON.parse((e as MessageEvent).data) as AgentEvent;
        setEvents((prev) => [ev, ...prev.filter((p) => p.id !== ev.id)].slice(0, 300));
        refreshSoon();
      });
      stream.addEventListener("vehicle", (e) => callbacks.current.onVehicle(JSON.parse((e as MessageEvent).data)));
      stream.addEventListener("hazard", () => callbacks.current.onHazardChange());
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(first);
      clearInterval(poll);
      if (pending) clearTimeout(pending);
      if (retry) clearTimeout(retry);
      source?.close();
    };
  }, [refreshPlans]);

  async function select(id: string) {
    selected.current = id;
    setAllRoutes(false);
    try {
      const d = await plansApi.get(id);
      setDetail(d);
      onSelectPlan(d);
    } catch (err) {
      setMsg((err as Error).message);
    }
  }

  async function decide(decision: "approved" | "rejected") {
    if (!detail) return;
    try {
      await plansApi.decide(detail.id, decision, account.name, reason.trim());
      setMsg(`Plan ${decision}.`);
      setReason("");
      await refreshPlans();
    } catch (err) {
      setMsg((err as Error).message);
    }
  }

  async function planAgain() {
    if (!detail) return;
    try {
      const fresh = await plansApi.planAgain(detail.hazard_ids, account.name);
      setMsg("Planning again. It starts in a moment.");
      await refreshPlans();
      await select(fresh.id);
    } catch (err) {
      setMsg((err as Error).message);
    }
  }

  const awaiting = plans.filter((p) => p.status === "awaiting_approval").length;
  const evacuating = evacuations.filter((e) => e.stage === "waiting" || e.stage === "on_the_way" || e.stage === "on_board").length;
  const shown = selectedPlanId ? events.filter((e) => e.plan_id === selectedPlanId) : events;
  return (
    <aside className="pane right">
      <nav className="tabs" aria-label="Right panel">
        {TABS.map((t) => (
          <button key={t} className={tab === t ? "tab active" : "tab"} onClick={() => setTab(t)}>
            {t}{t === "Plans" && awaiting > 0 && <span className="count">{awaiting}</span>}
            {t === "Evacuations" && evacuating > 0 && <span className="count">{evacuating}</span>}
          </button>
        ))}
      </nav>
      <div className="pane-body">
        {live === "reconnecting" && <div className="alert danger small">Live updates interrupted. Retrying; this panel may be out of date.</div>}
        {degraded && <div className="alert danger small">The server reports: {degraded}.</div>}
        {msg && <div className="alert small">{msg}</div>}

        {tab === "Plans" && (
          <>
            {plans.length === 0 && <p className="empty">No plans yet. Draw a drill on the map, or wait for a severe alert over a populated area.</p>}
            <ul className="plan-list">
              {plans.map((p) => (
                <li key={p.id}>
                  <button className={p.id === selectedPlanId ? "plan selected" : "plan"} data-plan={p.id} onClick={() => select(p.id)}>
                    <span className={`status ${STATUS_TONES[p.status] ?? "grey"}`}>{p.status.replace("_", " ")}</span>
                    {p.is_drill && <span className="badge drill">DRILL</span>}
                    {p.parent_plan_id && <span className="badge replan">REROUTE</span>}
                    {p.evacuation && <span className="badge evacuation">EVACUATION</span>}
                    <span className="small muted plan-time">{ago(p.created_at)}</span>
                  </button>
                </li>
              ))}
            </ul>

            {detail && (
              <section className="plan-detail">
                {detail.summary && <p>{detail.summary}</p>}
                {!detail.requires_approval && !detail.evacuation && !detail.parent_plan_id && (detail.status === "approved" || detail.status === "dispatched") && (
                  <div className="alert ok small">Approved by the rules: nothing about this plan is unclear.</div>
                )}
                {detail.triage && <p className="small muted">Priority {detail.triage.priority} · plan for {detail.triage.planning_horizon_hours} h</p>}
                {detail.unreachable_reason && <div className="alert danger small">{detail.unreachable_reason}</div>}
                {(detail.status === "failed" || detail.status === "unreachable") && !detail.parent_plan_id && (
                  <button onClick={planAgain}>Plan again</button>
                )}
                {detail.escalations.length > 0 && (
                  <div className="alert warn small">
                    <b>Needs a person</b>
                    <ul>{escalationLines(detail.escalations).slice(0, MAX_LINES).map((line) => <li key={line}>{line}</li>)}</ul>
                    {escalationLines(detail.escalations).length > MAX_LINES && <small>and {escalationLines(detail.escalations).length - MAX_LINES} more</small>}
                  </div>
                )}
                {detail.routes.slice(0, allRoutes ? undefined : MAX_LINES).map((r) => {
                  const truck = vehicles.find((v) => v.id === r.vehicle_id);
                  return (
                    <div className="route-card" key={r.id}>
                      {truck && (
                        <div className="row-between">
                          <span>
                            <span className="truck-no">{truck.fleet_number}</span><b>{plate(truck.label)}</b>{truck.simulated && <span className="badge sim">SIMULATED</span>}
                            {truck.driver_name && <small className="block muted">{truck.driver_name}</small>}
                          </span>
                          <a className="link" href={`/driver-view?vehicle=${truck.id}`} target="_blank" rel="noreferrer">Driver screen</a>
                        </div>
                      )}
                      <b>{(r.distance_m / 1000).toFixed(0)} km</b> · {(r.duration_s / 3600).toFixed(1)} h{r.kind === "evacuation" ? " · evacuation" : ` · ${(r.load_kg / 1000).toFixed(1)} t`}
                      <div className="progress" aria-label={`${r.completed_stops} of ${r.stops.length} stops`}>
                        <span style={{ width: `${(100 * r.completed_stops) / r.stops.length}%` }} />
                      </div>
                      <small className="muted">{r.completed_stops}/{r.stops.length} stops · {r.status} · keeps {r.min_hazard_clearance_m?.toFixed(0) ?? "?"} m from hazards</small>
                    </div>
                  );
                })}
                {detail.routes.length > MAX_LINES && !allRoutes && <button className="link" onClick={() => setAllRoutes(true)}>Show all {detail.routes.length} trucks</button>}
                {detail.status === "awaiting_approval" && (
                  <div className="approve">
                    <b>Your decision is needed</b>
                    {detail.needs_person.length > 0 && <ul className="small">{detail.needs_person.map((why) => <li key={why}>{why}</li>)}</ul>}
                    <small className="muted">Recorded as {account.name} ({account.email}).</small>
                    <input placeholder="Reason (optional)" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={1000} />
                    <div className="row">
                      <button className="primary" onClick={() => decide("approved")}>Approve dispatch</button>
                      <button className="danger" onClick={() => decide("rejected")}>Reject</button>
                    </div>
                  </div>
                )}
                {detail.approval && (
                  <p className="small"><b>{detail.approval.decision}</b> by {detail.approval.decided_by}, {ago(detail.approval.decided_at)}</p>
                )}
              </section>
            )}
          </>
        )}

        {tab === "Evacuations" && (
          <>
            <p className="small muted">People collected just outside a zone and taken to a safe point. Click a drill or alert on the map to request one.</p>
            <ul className="list">
              {evacuations.map((e) => {
                const truck = vehicles.find((v) => v.id === e.vehicle_id);
                return (
                  <li key={e.id}>
                    <span className={`status ${EVACUATION_STAGES[e.stage].tone}`}>{EVACUATION_STAGES[e.stage].text}</span>
                    {e.is_drill && <span className="badge drill">DRILL</span>}
                    {e.simulated && <span className="badge sim">SIMULATED</span>}
                    <b className="block">{e.people} people from {e.settlement}</b>
                    <small className="block muted">{e.zone.slice(0, 70)}</small>
                    {e.pickup_name && <small className="block">{e.pickup_name} → {e.safe_name}</small>}
                    {e.zones_avoided !== null && (
                      <small className="block muted">
                        Way out avoids {e.zones_avoided} active zone{e.zones_avoided === 1 ? "" : "s"}{e.zones_crossed ? `, crosses ${e.zones_crossed} with no way round` : ""}
                      </small>
                    )}
                    {e.failure && <div className="alert danger small">{e.failure}</div>}
                    {truck && (
                      <div className="row">
                        <span className="small"><span className="truck-no">{truck.fleet_number}</span>{truck.driver_name ?? plate(truck.label)}</span>
                        <a className="link" href={`/driver-view?vehicle=${truck.id}`} target="_blank" rel="noreferrer">Driver screen</a>
                      </div>
                    )}
                  </li>
                );
              })}
              {evacuations.length === 0 && <li className="empty">No evacuations yet. In presentation mode one is requested every 10 to 15 minutes while a zone is active.</li>}
            </ul>
          </>
        )}

        {tab === "Activity" && (
          <>
            <p className="small muted">{selectedPlanId ? "Showing the selected plan." : "Every step the system takes, as it happens."}</p>
            <ol className="timeline">
              {shown.map((e) => (
                <li key={e.id} className={`ev ${e.event_type}`}>
                  <span className="ev-icon" aria-hidden="true">{EVENT_ICONS[e.event_type] ?? "•"}</span>
                  <span>
                    <span className="ev-head"><b>{e.node}</b> <small className="muted">{new Date(e.created_at).toLocaleTimeString()}</small></span>
                    <span className="small block">{e.message}</span>
                  </span>
                </li>
              ))}
              {shown.length === 0 && <li className="empty">Nothing yet. Steps appear here the moment they happen.</li>}
            </ol>
          </>
        )}

        {tab === "Team" && <TeamPanel me={account} />}
      </div>
    </aside>
  );
}
