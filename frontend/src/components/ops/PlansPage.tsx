"use client";

// Plans and the human approval gate. Pick a plan on the left; everything about it opens on the right. Approving
// asks the backend to dispatch, and the backend checks the plan against the current hazards again first.

import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Escalation, PlanDetail, plansApi } from "@/lib/api";
import { ago, itemName, plate } from "@/lib/format";

import { ActionButton, AlertCard, ConfirmDialog, EmptyState, FilterChips, PageHeader, StatusBadge, Surface } from "../ui";
import styles from "./ops.module.css";
import { useOps } from "./OpsProvider";
import { PlanStatus } from "./parts";

const LiveMap = dynamic(() => import("../LiveMap"), { ssr: false, loading: () => <div className="map map-loading">Loading map…</div> });
const NO_ROUTES: never[] = [];

const FILTERS = [
  { key: "awaiting", label: "Awaiting approval", statuses: ["awaiting_approval"] },
  { key: "active", label: "In progress", statuses: ["pending", "planning"] },
  { key: "approved", label: "Approved", statuses: ["approved", "dispatched"] },
  { key: "failed", label: "Failed", statuses: ["failed", "rejected"] },
  { key: "unreachable", label: "Unreachable", statuses: ["unreachable"] },
  { key: "all", label: "All", statuses: null },
] as const;
type FilterKey = (typeof FILTERS)[number]["key"];
const MAX_LINES = 3;

/** The same shortfall at each relief point reads as noise: one line per item and reason, with the total. */
/** What to say when a plan has no summary: a plan that ended must not read as if it were still being prepared. */
const NO_SUMMARY: Record<string, string> = {
  failed: "This plan could not be completed. Open it to see why.",
  unreachable: "No safe road route was found. Open it to see why.",
  superseded: "Replaced by a newer plan.",
  rejected: "Rejected by a duty officer.",
};
const BLOCKED_TITLE: Record<string, string> = { failed: "This plan could not be completed", unreachable: "No safe road route" };

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
    (l) => `${Math.round(l.quantity).toLocaleString()} ${itemName(l.item)}${l.sites > 1 ? ` across ${l.sites} sites` : ""}: ${l.reason}`,
  );
  return [...others, ...grouped];
}

function moreWillGo(plan: PlanDetail): boolean {
  const retrying = plan.next_wave && plan.status === "failed";
  return plan.waves_left > 0 && (retrying || ["awaiting_approval", "approved", "dispatched"].includes(plan.status));
}

function sentByRules(escalation: Escalation): boolean {
  return "quantity_short" in escalation && !escalation.reason.startsWith("still on the");
}

function Gaps({ title, note, escalations }: { title: string; note?: string; escalations: Escalation[] }) {
  const lines = escalationLines(escalations);
  if (lines.length === 0) return null;
  return (
    <div className="alert ok small">
      <b>{title}</b>
      {note && <div>{note}</div>}
      <ul>{lines.slice(0, MAX_LINES).map((line) => <li key={line}>{line}</li>)}</ul>
      {lines.length > MAX_LINES && <small>and {lines.length - MAX_LINES} more</small>}
    </div>
  );
}

export default function PlansPage() {
  const router = useRouter();
  const params = useSearchParams();
  const selected = params.get("plan");
  const { account, plans, vehicles, hazards, liveRoutes, events, bounds, plansVersion, plansError, refreshPlans } = useOps();
  const [filter, setFilter] = useState<FilterKey | null>(null);
  const [loaded, setLoaded] = useState<{ id: string; plan: PlanDetail } | null>(null);
  const [loadError, setLoadError] = useState<{ id: string; text: string } | null>(null);
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; ok: boolean } | null>(null);
  const [allRoutes, setAllRoutes] = useState(false);

  const awaitingCount = plans.filter((p) => p.status === "awaiting_approval").length;
  const active: FilterKey = filter ?? (awaitingCount > 0 ? "awaiting" : "all");
  const statuses = FILTERS.find((f) => f.key === active)?.statuses;
  const list = plans.filter((p) => !statuses || (statuses as readonly string[]).includes(p.status));
  const countOf = (key: FilterKey) => { const s = FILTERS.find((f) => f.key === key)!.statuses; return plans.filter((p) => !s || (s as readonly string[]).includes(p.status)).length; };

  // Reads the open plan again whenever the plan list refreshes, so a decision made elsewhere shows up here.
  useEffect(() => {
    if (!selected) return;
    let current = true;
    plansApi.get(selected).then(
      (plan) => { if (current) { setLoaded({ id: selected, plan }); setLoadError(null); } },
      (err) => { if (current) setLoadError({ id: selected, text: (err as Error).message }); },
    );
    return () => { current = false; };
  }, [selected, plansVersion]);

  const detail = loaded && loaded.id === selected ? loaded.plan : null;
  const error = loadError && loadError.id === selected ? loadError.text : null;
  const reasoning = useMemo(() => events.filter((e) => e.plan_id === selected).slice(0, 12), [events, selected]);
  const demandByItem = useMemo(() => {
    const totals = new Map<string, number>();
    for (const d of detail?.demand ?? []) totals.set(d.item_code, (totals.get(d.item_code) ?? 0) + d.quantity);
    return [...totals.entries()];
  }, [detail]);

  function select(id: string) {
    setNote(null);
    setReason("");
    setAllRoutes(false);
    router.replace(`/dashboard/plans?plan=${id}`, { scroll: false });
  }

  async function decide(decision: "approved" | "rejected") {
    if (!detail) return;
    setBusy(true);
    try {
      await plansApi.decide(detail.id, decision, account.name, reason.trim());
      setNote({ text: decision === "approved" ? "Plan approved. The backend is checking it against current hazards before it dispatches." : "Plan rejected.", ok: true });
      setReason("");
      await refreshPlans();
    } catch (err) {
      setNote({ text: (err as Error).message, ok: false });
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  async function planAgain() {
    if (!detail) return;
    setBusy(true);
    try {
      const fresh = await plansApi.planAgain(detail.hazard_ids, account.name);
      setNote({ text: "Planning again. It starts in a moment.", ok: true });
      await refreshPlans();
      select(fresh.id);
    } catch (err) {
      setNote({ text: (err as Error).message, ok: false });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title="Plans & Approvals" subtitle="Clear plans go out by the rules. A duty officer decides when a plan needs a person." />
      {plansError && <AlertCard tone="bad" title="Plans unavailable">{plansError}</AlertCard>}
      <FilterChips label="Plan status" value={active} onChange={setFilter}
        options={FILTERS.map((f) => ({ key: f.key, label: f.label, count: countOf(f.key) }))} />

      <div className={`${styles.cols} ${styles.splitPlans}`}>
        <div className={styles.master} aria-label="Plans">
          {list.length === 0 && (
            <Surface>
              <EmptyState icon="plans" title="No plans here">
                {plans.length === 0 ? "No plans yet. Draw a drill in What-If Simulation, or wait for a severe alert over a populated area." : "No plans have this status."}
              </EmptyState>
            </Surface>
          )}
          {list.map((p) => (
            <button key={p.id} type="button" data-plan={p.id} className={`${styles.item} ${p.id === selected ? styles.itemOn : ""}`} onClick={() => select(p.id)}>
              <span className={styles.itemTop}>
                <span className={styles.tags}>
                  <PlanStatus status={p.status} />
                  {p.is_drill && <StatusBadge tone="drill">DRILL</StatusBadge>}
                  {p.parent_plan_id && <StatusBadge tone="info">Reroute</StatusBadge>}
                  {p.evacuation && <StatusBadge tone="good">Evacuation</StatusBadge>}
                  {p.next_wave && <StatusBadge tone="info">NEXT WAVE</StatusBadge>}
                </span>
                <small className={styles.muted}>{ago(p.created_at)}</small>
              </span>
              <span className={styles.itemText}>{(p.summary ?? NO_SUMMARY[p.status] ?? "Plan is being prepared.").slice(0, 120)}</span>
            </button>
          ))}
        </div>

        <div className={styles.detail}>
          {!selected && <Surface><EmptyState icon="plans" title="Choose a plan">Select a plan on the left to see its routes, supplies, reasoning and decision.</EmptyState></Surface>}
          {selected && error && <AlertCard tone="bad" title="Could not open this plan">{error}</AlertCard>}
          {selected && !detail && !error && <Surface><p className={styles.muted}>Opening the plan…</p></Surface>}
          {note && <AlertCard tone={note.ok ? "good" : "bad"}>{note.text}</AlertCard>}

          {detail && (
            <div className={styles.detail} data-testid="plan-detail">
              <Surface>
                <div className={styles.detailTitle}>
                  <span className={styles.tags}>
                    <PlanStatus status={detail.status} />
                    {detail.is_drill && <StatusBadge tone="drill">DRILL</StatusBadge>}
                    {detail.parent_plan_id && <StatusBadge tone="info">Reroute</StatusBadge>}
                    {detail.evacuation && <StatusBadge tone="good">Evacuation</StatusBadge>}
                  </span>
                  <small className={styles.muted}>Created {ago(detail.created_at)} · updated {ago(detail.updated_at)}</small>
                </div>
                <p style={{ marginTop: 10 }}>{detail.summary ?? NO_SUMMARY[detail.status] ?? "The plan is still being prepared."}</p>
                {detail.is_drill && <p className={styles.muted} style={{ marginTop: 6, fontSize: 13 }}>This plan comes from a what-if drill. Its routes are marked DRILL on the driver&apos;s screen.</p>}
                {!detail.requires_approval && !detail.evacuation && !detail.parent_plan_id && (detail.status === "approved" || detail.status === "dispatched") && (
                  <AlertCard tone="good" title="Approved by the rules">Nothing about this plan is unclear.</AlertCard>
                )}
              </Surface>

              {detail.status === "awaiting_approval" && (
                <div className={styles.decision}>
                  <b>Your decision is needed</b>
                  <small className={styles.muted}>Recorded as {account.name} ({account.email}).</small>
                  <input placeholder="Reason (optional)" aria-label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={1000} />
                  <div className={styles.decisionActions}>
                    <ActionButton variant="primary" icon="check" onClick={() => setConfirming(true)}>Approve dispatch</ActionButton>
                    <ActionButton variant="danger" busy={busy} onClick={() => decide("rejected")}>Reject</ActionButton>
                  </div>
                </div>
              )}
              {detail.approval && (
                <Surface title="Decision">
                  <p><b style={{ textTransform: "capitalize" }}>{detail.approval.decision}</b> by {detail.approval.decided_by}, {ago(detail.approval.decided_at)}</p>
                  {detail.approval.reason && <p className={styles.muted}>Reason: {detail.approval.reason}</p>}
                </Surface>
              )}

              {detail.unreachable_reason && <AlertCard tone="bad" title={BLOCKED_TITLE[detail.status] ?? "Why this plan stopped"}>{detail.unreachable_reason}</AlertCard>}
              {(detail.status === "failed" || detail.status === "unreachable") && !detail.parent_plan_id && (
                <div><ActionButton icon="refresh" busy={busy} onClick={planAgain}>Plan again</ActionButton></div>
              )}
              {moreWillGo(detail) && (
                <Gaps title="Still to send" escalations={detail.escalations.filter(sentByRules)}
                  note={detail.status === "failed" ? "The rules try again in a few minutes." : "The rules send the next wave when these trucks finish."} />
              )}
              {(detail.escalations.length > 0 || detail.needs_person.length > 0) && (
                <AlertCard tone="warn" title="Needs a person">
                  {escalationLines(moreWillGo(detail) ? detail.escalations.filter((e) => !sentByRules(e)) : detail.escalations).length > 0 && (
                    <ul>{escalationLines(moreWillGo(detail) ? detail.escalations.filter((e) => !sentByRules(e)) : detail.escalations).map((line) => <li key={line}>{line}</li>)}</ul>
                  )}
                  {detail.needs_person.length > 0 && <ul>{detail.needs_person.map((reason) => <li key={reason}>{reason}</li>)}</ul>}
                </AlertCard>
              )}

              <Surface title={`Delivery routes (${detail.routes.length})`}>
                {detail.routes.length === 0 ? <p className={styles.muted}>Routes are written when a plan is approved and dispatched.</p> : (
                  <div className={styles.stackTight}>
                    <div className={styles.stage + " " + styles.stageShort}>
                      <LiveMap hazards={hazards} initialBounds={bounds} drawing={false} draftVertices={[]} onMapClick={() => undefined} onCancelDrill={() => undefined}
                        onFacilitiesStatus={() => undefined} onFindDrivers={() => undefined} onAskTruck={() => undefined} onEvacuate={() => undefined}
                        vehicles={vehicles} routes={detail.routes} liveRoutes={liveRoutes.length ? liveRoutes : NO_ROUTES} />
                    </div>
                    {detail.routes.slice(0, allRoutes ? undefined : MAX_LINES).map((r) => {
                      const truck = vehicles.find((v) => v.id === r.vehicle_id);
                      return (
                        <div className={styles.routeCard} key={r.id} data-testid="route-card">
                          <div className="row-between">
                            <span>
                              {truck ? <><span className="truck-no">{truck.fleet_number}</span><b>{plate(truck.label)}</b>{truck.simulated && <span className="badge sim">SIMULATED</span>}</> : <b>Truck</b>}
                              {truck?.driver_name && <small className="block muted">{truck.driver_name}</small>}
                            </span>
                            {truck && <a className="link" href={`/driver-view?vehicle=${truck.id}`} target="_blank" rel="noreferrer">Driver screen</a>}
                          </div>
                          <span><b>{(r.distance_m / 1000).toFixed(0)} km</b> · {(r.duration_s / 3600).toFixed(1)} h{r.kind === "evacuation" ? " · evacuation" : ` · ${(r.load_kg / 1000).toFixed(1)} t`}</span>
                          <div className="progress" aria-label={`${r.completed_stops} of ${r.stops.length} stops`}><span style={{ width: `${(100 * r.completed_stops) / Math.max(1, r.stops.length)}%` }} /></div>
                          <small className={styles.muted}>{r.completed_stops}/{r.stops.length} stops · {r.status} · keeps {r.min_hazard_clearance_m?.toFixed(0) ?? "?"} m from hazards</small>
                          <ol className={styles.stops}>
                            {r.stops.map((s, i) => (
                              <li key={`${s.facility_id}-${i}`} className={i < r.completed_stops ? "done" : ""}>
                                <span>{s.name}</span><small>{s.kind} · by {new Date(s.eta).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</small>
                              </li>
                            ))}
                          </ol>
                        </div>
                      );
                    })}
                    {detail.routes.length > MAX_LINES && !allRoutes && <button type="button" className="link" onClick={() => setAllRoutes(true)}>Show all {detail.routes.length} trucks</button>}
                  </div>
                )}
              </Surface>

              <Surface title="Supply requirements">
                {demandByItem.length === 0 ? <p className={styles.muted}>No demand has been worked out for this plan.</p> : (
                  <ul className={styles.stackTight}>
                    {demandByItem.map(([code, qty]) => <li key={code} className="row-between"><span>{itemName(code)}</span><b>{Math.round(qty).toLocaleString()}</b></li>)}
                  </ul>
                )}
                {detail.orders.length > 0 && <p className={styles.muted} style={{ marginTop: 8, fontSize: 13 }}>{detail.orders.length} supplier {detail.orders.length === 1 ? "order" : "orders"} created from this plan.</p>}
              </Surface>

              <Surface title="AI reasoning and validation">
                {detail.triage ? (
                  <div className={styles.stackTight}>
                    <p><b>Priority {detail.triage.priority}</b>, {detail.triage.planning_horizon_hours} h horizon.</p>
                    <p>{detail.triage.rationale}</p>
                    {detail.triage.key_risks.length > 0 && <ul style={{ paddingLeft: 18, listStyle: "disc" }}>{detail.triage.key_risks.map((k) => <li key={k}>{k}</li>)}</ul>}
                  </div>
                ) : <p className={styles.muted}>No triage was recorded for this plan.</p>}
                <h3 style={{ margin: "14px 0 6px", fontSize: 14 }}>Safety check</h3>
                {detail.critic.length === 0 ? <p className={styles.muted}>The safety check has not run yet.</p> : (
                  <ul className={styles.stackTight}>
                    {detail.critic.map((c) => (
                      <li key={c.attempt}>
                        <StatusBadge tone={c.passed ? "good" : "bad"}>Attempt {c.attempt}: {c.passed ? "passed" : `rejected (${c.violations.length})`}</StatusBadge>
                        {!c.passed && <ul style={{ margin: "6px 0 0", paddingLeft: 18, listStyle: "disc", fontSize: 13 }}>{c.violations.map((v) => <li key={v}>{v}</li>)}</ul>}
                      </li>
                    ))}
                  </ul>
                )}
                {reasoning.length > 0 && (
                  <>
                    <h3 style={{ margin: "14px 0 6px", fontSize: 14 }}>Latest steps</h3>
                    <ul className={styles.stackTight}>{reasoning.map((e) => <li key={e.id} className="small"><b>{e.node}</b> <span className={styles.muted}>{new Date(e.created_at).toLocaleTimeString()}</span><br />{e.message}</li>)}</ul>
                    <Link href="/dashboard/activity" className={styles.link} style={{ display: "inline-block", marginTop: 8 }}>Open the full activity timeline</Link>
                  </>
                )}
              </Surface>
            </div>
          )}
        </div>
      </div>

      <ConfirmDialog open={confirming} title="Dispatch this plan?" confirmLabel="Yes, dispatch" busy={busy} onCancel={() => setConfirming(false)} onConfirm={() => decide("approved")}>
        <p>Trucks will be sent on the routes shown. The backend checks the plan against the current hazards again before anything is dispatched. Recorded as {account.name}.</p>
      </ConfirmDialog>
    </>
  );
}
