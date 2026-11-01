"use client";

// The duty officer's frame: who is signed in, the pages they can reach, and the live-connection state.
// Real problems (lost connection, a paused stream, presentation mode) are always shown here, never hidden.

import { usePathname } from "next/navigation";
import { ReactNode } from "react";

import { useAccount, signOut } from "@/lib/session";

import { Loading } from "../AppShell";
import { AlertCard, AppLayout, ConnectionStatus, NavItem } from "../ui";
import { OpsProvider, useOps } from "./OpsProvider";

const PAGES = [
  { key: "command", label: "Command Center", icon: "map", href: "/dashboard" },
  { key: "hazards", label: "Hazards & Alerts", icon: "hazard", href: "/dashboard/hazards" },
  { key: "plans", label: "Plans & Approvals", icon: "plans", href: "/dashboard/plans" },
  { key: "fleet", label: "Fleet & Drivers", icon: "truck", href: "/dashboard/fleet" },
  { key: "evacuations", label: "Evacuations", icon: "evac", href: "/dashboard/evacuations" },
  { key: "sources", label: "Data Sources", icon: "database", href: "/dashboard/sources" },
  { key: "activity", label: "Activity & AI", icon: "activity", href: "/dashboard/activity" },
  { key: "team", label: "Team & Access", icon: "users", href: "/dashboard/team" },
  { key: "directory", label: "Directory", icon: "book", href: "/directory" },
  { key: "simulation", label: "What-If Simulation", icon: "flask", href: "/dashboard/simulation" },
] as const;

function isActive(path: string, href: string): boolean {
  return href === "/dashboard" ? path === "/dashboard" : path === href || path.startsWith(`${href}/`);
}

export default function OpsShell({ children }: { children: ReactNode }) {
  const account = useAccount("operator");
  if (!account) return <Loading />;
  return (
    <OpsProvider account={account}>
      <Frame>{children}</Frame>
    </OpsProvider>
  );
}

function Frame({ children }: { children: ReactNode }) {
  const path = usePathname();
  const { account, plans, evacuations, health, demo, vehicles, failed, updatedAt, stream, streamMessage } = useOps();
  const awaiting = plans.filter((p) => p.status === "awaiting_approval").length;
  const evacuating = evacuations.filter((e) => e.stage === "waiting" || e.stage === "on_the_way" || e.stage === "on_board").length;
  const stale = health.filter((s) => s.is_stale || s.breaker_state !== "closed").length;
  const badges: Record<string, number> = { plans: awaiting, evacuations: evacuating, sources: stale };
  const items: NavItem[] = PAGES.map((p) => ({ key: p.key, label: p.label, icon: p.icon, href: p.href, active: isActive(path, p.href), badge: badges[p.key] }));
  const current = PAGES.find((p) => isActive(path, p.href))?.label ?? "Operations";
  const simulated = vehicles.filter((v) => v.simulated).length;
  const wide = path === "/dashboard/simulation";
  const banners = failed || stream === "reconnecting" || streamMessage || demo?.enabled;

  return (
    <AppLayout items={items} title={current} wide={wide} accountName={account.name} accountDetail="Duty officer" onSignOut={signOut}
      status={<ConnectionStatus updatedAt={updatedAt} failed={failed} degraded={stream === "reconnecting"} />}>
      {banners && (
        <div style={{ display: "grid", gap: 10, padding: wide ? "12px 20px 0" : 0 }}>
          {failed && <AlertCard tone="bad" title="Cannot reach the server">These numbers may be out of date. Retrying every 5 seconds.</AlertCard>}
          {!failed && stream === "reconnecting" && <AlertCard tone="warn" title="Live updates interrupted">Retrying. Plans and trucks may be out of date until it reconnects.</AlertCard>}
          {streamMessage && <AlertCard tone="warn" title="The server reports a problem">{streamMessage}</AlertCard>}
          {demo?.enabled && (
            <AlertCard tone="warn" title="Presentation mode">
              {simulated} simulated trucks. Their drivers, number plates and GPS are made up, and so is the stock at depots marked TEST-SIM. Alerts, roads, ration stock and places are live.
            </AlertCard>
          )}
        </div>
      )}
      {children}
    </AppLayout>
  );
}
