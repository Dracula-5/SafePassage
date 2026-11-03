"use client";

// The Command Center's right-hand panel: every plan by status, evacuations, the agents' latest steps and the team,
// beside the map. Plans, evacuations and steps come from useOps(), so they match every other page; the team is
// read here, and only while its tab is open. A row opens its own page for the full detail and any decision.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { teamApi, TeamMember } from "@/lib/api";
import { ago } from "@/lib/format";

import { REFRESH_MS } from "../LiveStatus";
import { FilterChips, StatusBadge } from "../ui";
import styles from "./ops.module.css";
import { useOps } from "./OpsProvider";
import { EVACUATION_STAGES, PlanStatus, planText } from "./parts";

type Tab = "plans" | "evacuations" | "activity" | "team";

// In the order a plan moves through them. Approved plans are about to leave, so they count as active.
const PLAN_GROUPS = [
  { key: "all", label: "All", statuses: null },
  { key: "awaiting", label: "Awaiting", statuses: ["awaiting_approval"] },
  { key: "active", label: "Active", statuses: ["pending", "planning", "approved"] },
  { key: "dispatched", label: "Dispatched", statuses: ["dispatched"] },
  { key: "replaced", label: "Replaced", statuses: ["superseded"] },
  { key: "stopped", label: "Stopped", statuses: ["failed", "rejected", "unreachable"] },
] as const;
type PlanGroup = (typeof PLAN_GROUPS)[number]["key"];

const UNDER_WAY = new Set(["waiting", "on_the_way", "on_board"]);
const ROLE_NAMES = { operator: "Duty officer", driver: "Driver", supplier: "Supplier" };
const MAX_EVENTS = 40;

function inGroup(status: string, group: PlanGroup): boolean {
  const statuses = PLAN_GROUPS.find((g) => g.key === group)?.statuses;
  return !statuses || (statuses as readonly string[]).includes(status);
}

/** A plan picked here is shown on the map beside it (CommandCenter): `selectedRoutes` is its route count, null while loading. */
export default function LivePanel({ selectedPlanId, selectedRoutes, onSelectPlan }: {
  selectedPlanId: string | null;
  selectedRoutes: number | null;
  onSelectPlan: (id: string | null) => void;
}) {
  const { plans, evacuations, events, plansError } = useOps();
  const [tab, setTab] = useState<Tab>("plans");
  const [group, setGroup] = useState<PlanGroup>("all");

  const underWay = evacuations.filter((e) => UNDER_WAY.has(e.stage)).length;
  // The plan counts are on the status chips; the tab counts only what is under way, so all four tabs fit a narrow panel.
  const tabs: { key: Tab; label: string; count?: number }[] = [
    { key: "plans", label: "Plans" },
    { key: "evacuations", label: "Evacuations", count: underWay || undefined },
    { key: "activity", label: "Activity" },
    { key: "team", label: "Team" },
  ];
  const shown = plans.filter((p) => inGroup(p.status, group));

  return (
    <section className={styles.panel} aria-label="Live operations">
      <div className={styles.panelTabs} role="tablist" aria-label="Live operations">
        {tabs.map((t) => (
          <button key={t.key} type="button" role="tab" id={`live-${t.key}`} aria-selected={tab === t.key} aria-controls="live-panel-body"
            className={`${styles.panelTab} ${tab === t.key ? styles.panelTabOn : ""}`} onClick={() => setTab(t.key)}>
            {t.label}{t.count !== undefined && <small>{t.count}</small>}
          </button>
        ))}
      </div>

      <div className={styles.panelBody} id="live-panel-body" role="tabpanel" aria-labelledby={`live-${tab}`}>
        {tab === "plans" && (
          <>
            <FilterChips label="Plan status" value={group} onChange={setGroup}
              options={PLAN_GROUPS.map((g) => ({ key: g.key, label: g.label, count: plans.filter((p) => inGroup(p.status, g.key)).length }))} />
            {plansError && <p className={styles.panelNote}>Plans unavailable: {plansError}</p>}
            {shown.length === 0 && !plansError && (
              <p className={styles.panelNote}>{plans.length === 0 ? "No plans yet. One starts when a severe alert covers a populated area, or from a drill." : "No plans have this status."}</p>
            )}
            {shown.map((p) => {
              const on = p.id === selectedPlanId;
              return (
                <div key={p.id} className={styles.panelPick}>
                  <button type="button" className={`${styles.panelRow} ${on ? styles.panelRowOn : ""}`} aria-pressed={on}
                    onClick={() => onSelectPlan(on ? null : p.id)}>
                    <span className={styles.itemTop}>
                      <span className={styles.tags}>
                        <PlanStatus status={p.status} />
                        {p.is_drill && <StatusBadge tone="drill">DRILL</StatusBadge>}
                        {p.evacuation && <StatusBadge tone="good">Evacuation</StatusBadge>}
                        {p.next_wave && <StatusBadge tone="info">NEXT WAVE</StatusBadge>}
                      </span>
                      <small className={styles.muted}>{ago(p.created_at)}</small>
                    </span>
                    <span className={styles.panelText}>{planText(p, 110)}</span>
                  </button>
                  {on && (
                    <div className={styles.panelPickActions}>
                      <small className={styles.muted}>
                        {selectedRoutes === null ? "Loading its routes…"
                          : selectedRoutes > 0 ? `${selectedRoutes} route${selectedRoutes === 1 ? "" : "s"} shown on the map`
                          : "No route yet: routes appear once a plan is dispatched"}
                      </small>
                      <span className={styles.tags}>
                        <Link href={`/dashboard/plans?plan=${p.id}`} className={styles.link}>Open plan details</Link>
                        <button type="button" className={styles.link} onClick={() => onSelectPlan(null)}>Show the whole region</button>
                      </span>
                    </div>
                  )}
                </div>
              );
            })}
          </>
        )}

        {tab === "evacuations" && (
          <>
            {evacuations.length === 0 && <p className={styles.panelNote}>No evacuations have been requested.</p>}
            {evacuations.map((e) => {
              const stage = EVACUATION_STAGES[e.stage];
              return (
                <Link key={e.id} href="/dashboard/evacuations" className={styles.panelRow}>
                  <span className={styles.itemTop}>
                    <span className={styles.tags}>
                      <StatusBadge tone={stage.tone}>{stage.text}</StatusBadge>
                      {e.is_drill && <StatusBadge tone="drill">DRILL</StatusBadge>}
                      {e.simulated && <StatusBadge tone="warn">SIMULATED</StatusBadge>}
                    </span>
                    <small className={styles.muted}>{ago(e.created_at)}</small>
                  </span>
                  <span className={styles.panelText}><b>{e.people} people</b> from {e.settlement}</span>
                </Link>
              );
            })}
          </>
        )}

        {tab === "activity" && (
          <>
            {events.length === 0 && <p className={styles.panelNote}>Steps appear here the moment the planning agents take them, from when this page opened.</p>}
            {events.slice(0, MAX_EVENTS).map((e) => {
              const row = (
                <>
                  <span className={styles.itemTop}>
                    <span className={styles.tags}><b className={styles.panelNode}>{e.node.replace(/_/g, " ")}</b><StatusBadge tone="plain">{e.event_type.replace(/_/g, " ")}</StatusBadge></span>
                    <small className={styles.muted}>{new Date(e.created_at).toLocaleTimeString()}</small>
                  </span>
                  <span className={styles.panelText}>{e.message.slice(0, 140)}</span>
                </>
              );
              return e.plan_id
                ? <Link key={e.id} href={`/dashboard/plans?plan=${e.plan_id}`} className={styles.panelRow}>{row}</Link>
                : <div key={e.id} className={styles.panelRow}>{row}</div>;
            })}
            <Link href="/dashboard/activity" className={styles.link}>All activity and filters</Link>
          </>
        )}

        {tab === "team" && <TeamList />}
      </div>
    </section>
  );
}

/** Who has an account and whether they can sign in. Changes to access are made on the Team & Access page. */
function TeamList() {
  const { account: me } = useOps();
  const [team, setTeam] = useState<TeamMember[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => teamApi.list().then((t) => { setTeam(t); setError(null); }, (err) => setError(err.message)), []);

  useEffect(() => {
    const first = setTimeout(load, 0);
    const timer = setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [load]);

  if (error) return <p className={styles.panelNote}>Could not load the team: {error}</p>;
  if (team === null) return <p className={styles.panelNote}>Loading the team…</p>;
  return (
    <>
      {team.map((p) => (
        <div key={p.id} className={styles.panelRow}>
          <span className={styles.itemTop}>
            <b>{p.name}{p.id === me.id && <small className={styles.muted}> (you)</small>}</b>
            {p.disabled ? <StatusBadge tone="bad">Access removed</StatusBadge> : !p.approved ? <StatusBadge tone="warn">Pending approval</StatusBadge> : <StatusBadge tone="good">Active</StatusBadge>}
          </span>
          <small className={styles.muted}>{ROLE_NAMES[p.role]}{p.vehicle_label || p.supplier_name ? ` · ${p.vehicle_label ?? p.supplier_name}` : ""} · last seen {ago(p.last_signin_at)}</small>
        </div>
      ))}
      <Link href="/dashboard/team" className={styles.link}>Manage the team</Link>
    </>
  );
}
