"use client";

// Right-hand operations panel: plans, the human approval gate, and the live agent timeline.
// The timeline comes from GET /api/stream (SSE, resumes with Last-Event-ID automatically).

import { useCallback, useEffect, useRef, useState } from "react";

import { AgentEvent, PlanDetail, plansApi, PlanSummary, streamUrl, Vehicle } from "@/lib/api";

const STATUS_COLORS: Record<string, string> = {
  pending: "#868e96", planning: "#1971c2", awaiting_approval: "#e8590c", approved: "#2f9e44", dispatched: "#2b8a3e",
  rejected: "#c92a2a", failed: "#c92a2a", unreachable: "#c92a2a", superseded: "#868e96",
};
const EVENT_ICONS: Record<string, string> = {
  node_started: "▶", node_finished: "■", tool_call: "⚙", tool_result: "⇣", decision: "✔", critic_rejection: "✖",
  approval_requested: "⏸", approval_decided: "👤", dispatch: "🚚", data_stale: "⚠", error: "⚠",
};

interface Props {
  selectedPlanId: string | null;
  onSelectPlan: (plan: PlanDetail | null) => void;
  onVehicle: (v: Vehicle) => void;
  onHazardChange: () => void;
}

export default function OpsPanel({ selectedPlanId, onSelectPlan, onVehicle, onHazardChange }: Props) {
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [detail, setDetail] = useState<PlanDetail | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [live, setLive] = useState<"connecting" | "live" | "reconnecting">("connecting");
  const [approver, setApprover] = useState("");
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const selected = useRef<string | null>(selectedPlanId);
  const callbacks = useRef({ onSelectPlan, onVehicle, onHazardChange });
  useEffect(() => {
    selected.current = selectedPlanId;
    callbacks.current = { onSelectPlan, onVehicle, onHazardChange };
  });

  const refreshPlans = useCallback(async () => {
    try {
      setPlans(await plansApi.list());
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
    const source = new EventSource(streamUrl());
    let pending: ReturnType<typeof setTimeout> | null = null;
    const refreshSoon = () => {
      if (pending) clearTimeout(pending);
      pending = setTimeout(refreshPlans, 500);
    };
    source.onopen = () => setLive("live");
    source.onerror = () => setLive("reconnecting");
    source.addEventListener("agent_event", (e) => {
      const ev = JSON.parse((e as MessageEvent).data) as AgentEvent;
      setEvents((prev) => [ev, ...prev.filter((p) => p.id !== ev.id)].slice(0, 300));
      refreshSoon();
    });
    source.addEventListener("vehicle", (e) => callbacks.current.onVehicle(JSON.parse((e as MessageEvent).data)));
    source.addEventListener("hazard", () => callbacks.current.onHazardChange());
    return () => {
      clearTimeout(first);
      if (pending) clearTimeout(pending);
      source.close();
    };
  }, [refreshPlans]);

  async function select(id: string) {
    selected.current = id;
    try {
      const d = await plansApi.get(id);
      setDetail(d);
      onSelectPlan(d);
    } catch (err) {
      setMsg((err as Error).message);
    }
  }

  async function decide(decision: "approved" | "rejected") {
    if (!detail || !approver.trim()) return;
    try {
      await plansApi.decide(detail.id, decision, approver.trim(), reason.trim());
      setMsg(`Plan ${decision}.`);
      setReason("");
      await refreshPlans();
    } catch (err) {
      setMsg((err as Error).message);
    }
  }

  const shown = events.filter((e) => !selectedPlanId || e.plan_id === selectedPlanId);

  return (
    <aside className="panel ops">
      <h2>
        Plans <span className={`live ${live}`}>{live === "live" ? "● live" : live === "connecting" ? "○ connecting" : "○ reconnecting…"}</span>
      </h2>
      {plans.length === 0 && <p className="muted small">No plans yet. Publish a drill or wait for a severe hazard.</p>}
      <ul className="plans">
        {plans.map((p) => (
          <li key={p.id} className={p.id === selectedPlanId ? "selected" : ""} onClick={() => select(p.id)}>
            <span className="badge" style={{ background: STATUS_COLORS[p.status] ?? "#495057" }}>{p.status.replace("_", " ")}</span>
            {p.is_drill && <span className="badge drill">DRILL</span>}
            <span className="small">{new Date(p.created_at).toLocaleTimeString()} · {p.trigger_kind}</span>
          </li>
        ))}
      </ul>

      {detail && (
        <section className="detail">
          {detail.summary && <p>{detail.summary}</p>}
          {detail.triage && (
            <p className="small"><b>Triage:</b> {detail.triage.priority}, {detail.triage.planning_horizon_hours} h. {detail.triage.rationale}</p>
          )}
          {detail.unreachable_reason && <div className="error small">{detail.unreachable_reason}</div>}
          {detail.escalations.length > 0 && (
            <div className="small warn">
              <b>Escalated to humans:</b>
              <ul>{detail.escalations.map((e, i) => <li key={i}>{e.quantity_short.toFixed(0)} {e.item_code}: {e.reason}</li>)}</ul>
            </div>
          )}
          {detail.critic.length > 0 && (
            <p className="small"><b>Critic:</b> {detail.critic.map((c) => `#${c.attempt} ${c.passed ? "pass" : "reject (" + c.violations.length + ")"}`).join(" → ")}</p>
          )}
          <p className="small">
            {detail.routes.length} route(s) · {detail.orders.length} order(s)
            {detail.routes.map((r) => ` · ${(r.distance_m / 1000).toFixed(0)} km, clearance ${r.min_hazard_clearance_m?.toFixed(0) ?? "?"} m`)}
          </p>
          {detail.status === "awaiting_approval" && (
            <div className="approve">
              <b>Human approval required</b>
              <input placeholder="Your name (recorded)" value={approver} onChange={(e) => setApprover(e.target.value)} maxLength={100} />
              <input placeholder="Reason (optional)" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={1000} />
              <div className="row">
                <button className="ok" disabled={!approver.trim()} onClick={() => decide("approved")}>Approve dispatch</button>
                <button className="danger" disabled={!approver.trim()} onClick={() => decide("rejected")}>Reject</button>
              </div>
            </div>
          )}
          {detail.approval && (
            <p className="small">Decision: <b>{detail.approval.decision}</b> by {detail.approval.decided_by} at {new Date(detail.approval.decided_at).toLocaleTimeString()}</p>
          )}
        </section>
      )}
      {msg && <p className="small">{msg}</p>}

      <h2>Agent reasoning {selectedPlanId ? "(selected plan)" : "(all)"}</h2>
      <ol className="timeline">
        {shown.map((e) => (
          <li key={e.id} className={`ev ${e.event_type}`}>
            <span className="icon">{EVENT_ICONS[e.event_type] ?? "•"}</span>
            <span className="node">{e.node}</span>
            <span className="small muted">{new Date(e.created_at).toLocaleTimeString()}</span>
            <div className="small">{e.message}</div>
            {e.source_ids.length > 0 && <div className="tiny muted">sources: {e.source_ids.slice(0, 4).join(", ")}{e.source_ids.length > 4 ? "…" : ""}</div>}
          </li>
        ))}
      </ol>
    </aside>
  );
}
