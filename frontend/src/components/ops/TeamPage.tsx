"use client";

// Everyone with an account. A duty officer can approve a new duty officer or driver (where the server asks for that),
// vouch for a supplier, make a one-time password link, or remove and restore access. The server decides who may.

import { useCallback, useEffect, useMemo, useState } from "react";

import { teamApi, TeamMember } from "@/lib/api";
import { ago } from "@/lib/format";

import { REFRESH_MS } from "../LiveStatus";
import { AlertCard, Column, ConfirmDialog, DataTable, EmptyState, FilterBar, FilterChips, PageHeader, SearchInput, StatusBadge, Surface } from "../ui";
import { matches } from "./constants";
import styles from "./ops.module.css";
import { useOps } from "./OpsProvider";

const ROLE_NAMES = { operator: "Duty officer", driver: "Driver", supplier: "Supplier" };
type RoleFilter = "all" | TeamMember["role"] | "pending";
type Pending = { kind: "remove"; person: TeamMember } | { kind: "withdraw"; person: TeamMember } | { kind: "link"; person: TeamMember; url: string; hours: number };

export default function TeamPage() {
  const { account: me } = useOps();
  const [team, setTeam] = useState<TeamMember[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);
  const [query, setQuery] = useState("");
  const [role, setRole] = useState<RoleFilter>("all");
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => teamApi.list().then((t) => { setTeam(t); setError(null); }, (err) => setError(err.message)), []);

  useEffect(() => {
    const first = setTimeout(load, 0);
    const timer = setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [load]);

  async function act(action: () => Promise<unknown>, success?: string) {
    setBusy(true);
    try {
      await action();
      if (success) setMsg({ text: success, ok: true });
      await load();
    } catch (err) {
      setMsg({ text: (err as Error).message, ok: false });
    } finally {
      setBusy(false);
      setPending(null);
    }
  }

  async function makeLink(person: TeamMember) {
    try {
      const { path, expires_in_hours } = await teamApi.resetLink(person.id);
      setPending({ kind: "link", person, url: `${window.location.origin}${path}`, hours: expires_in_hours });
    } catch (err) {
      setMsg({ text: (err as Error).message, ok: false });
    }
  }

  const all = useMemo(() => team ?? [], [team]);
  const shown = useMemo(() => all
    .filter((p) => role === "all" || (role === "pending" ? !p.approved : p.role === role))
    .filter((p) => matches(query, p.name, p.email, p.supplier_name, p.vehicle_label)), [all, role, query]);
  const awaiting = all.filter((p) => !p.approved && p.role !== "supplier").length;

  const columns: Column<TeamMember>[] = [
    { key: "person", header: "Person", render: (p) => <span className={`${styles.cell} person`}><b>{p.name}{p.id === me.id && <small className={styles.muted}> (you)</small>}</b><small>{p.email}{p.email_verified ? " · confirmed" : ""}</small></span> },
    { key: "role", header: "Role", render: (p) => <StatusBadge tone="info">{ROLE_NAMES[p.role]}</StatusBadge> },
    { key: "status", header: "Access", render: (p) => p.disabled ? <StatusBadge tone="bad">Access removed</StatusBadge> : !p.approved ? <StatusBadge tone="warn">Pending approval</StatusBadge> : <StatusBadge tone="good">Active</StatusBadge> },
    { key: "details", header: "Details", render: (p) => <span className="small">{p.vehicle_label ?? p.supplier_name ?? "–"}{p.supplier_id ? (p.supplier_verified ? " · verified" : " · unverified") : ""}</span> },
    { key: "seen", header: "Last seen", render: (p) => <span className="small">{ago(p.last_signin_at)}</span> },
    { key: "actions", header: "Actions", render: (p) => (
      <span className="row" style={{ gap: 4 }}>
        {!p.approved && p.role !== "supplier" && <button type="button" className="link" disabled={busy} onClick={() => act(() => teamApi.approve(p.id), `${p.name} approved.`)}>Approve account</button>}
        {p.supplier_id && <button type="button" className="link" disabled={busy} onClick={() => p.supplier_verified ? setPending({ kind: "withdraw", person: p }) : act(() => teamApi.verifySupplier(p.supplier_id!, true), `${p.supplier_name ?? p.name} verified.`)}>{p.supplier_verified ? "Withdraw verification" : "Verify supplier"}</button>}
        <button type="button" className="link" onClick={() => makeLink(p)}>Password link</button>
        {p.id !== me.id && (p.disabled
          ? <button type="button" className="link" disabled={busy} onClick={() => act(() => teamApi.setDisabled(p.id, false), `${p.name} can sign in again.`)}>Restore</button>
          : <button type="button" className="link danger" onClick={() => setPending({ kind: "remove", person: p })}>Remove access</button>)}
      </span>
    ) },
  ];

  return (
    <>
      <PageHeader title="Team & Access" subtitle="Duty officers, drivers and suppliers, and what each may do." />
      {error && <AlertCard tone="bad" title="Could not load the team">{error}</AlertCard>}
      {msg && <AlertCard tone={msg.ok ? "good" : "bad"}>{msg.text}</AlertCard>}
      {awaiting > 0 && <AlertCard tone="warn" title={`${awaiting} account${awaiting === 1 ? "" : "s"} waiting for approval`}>They cannot sign in until you approve them.</AlertCard>}
      <FilterBar>
        <SearchInput value={query} onChange={setQuery} placeholder="Search a name, email or organisation" />
        <FilterChips label="Role" value={role} onChange={setRole} options={[
          { key: "all", label: "Everyone", count: all.length }, { key: "operator", label: "Duty officers", count: all.filter((p) => p.role === "operator").length },
          { key: "driver", label: "Drivers", count: all.filter((p) => p.role === "driver").length }, { key: "supplier", label: "Suppliers", count: all.filter((p) => p.role === "supplier").length },
          ...(awaiting > 0 ? [{ key: "pending" as const, label: "Pending approval", count: awaiting }] : []),
        ]} />
      </FilterBar>
      <Surface flush>
        {team === null && !error ? <p className={styles.muted} style={{ padding: 20 }}>Loading the team…</p> : (
          <DataTable caption="People with accounts" columns={columns} rows={shown} rowKey={(p) => p.id}
            empty={<EmptyState icon="users" title="Nobody matches">Try a different search or role.</EmptyState>} />
        )}
      </Surface>

      <ConfirmDialog open={pending?.kind === "remove"} danger title="Remove access?" confirmLabel="Remove access" busy={busy} onCancel={() => setPending(null)}
        onConfirm={() => pending?.kind === "remove" && act(() => teamApi.setDisabled(pending.person.id, true), `${pending.person.name} can no longer sign in.`)}>
        <p>{pending?.kind === "remove" ? `${pending.person.name} is signed out at once and cannot sign in until you restore access.` : ""}</p>
      </ConfirmDialog>
      <ConfirmDialog open={pending?.kind === "withdraw"} danger title="Withdraw verification?" confirmLabel="Withdraw" busy={busy} onCancel={() => setPending(null)}
        onConfirm={() => pending?.kind === "withdraw" && pending.person.supplier_id && act(() => teamApi.verifySupplier(pending.person.supplier_id!, false), "Verification withdrawn.")}>
        <p>{pending?.kind === "withdraw" ? `${pending.person.supplier_name ?? pending.person.name} becomes unverified, and its stock link stops working too.` : ""}</p>
      </ConfirmDialog>
      <ConfirmDialog open={pending?.kind === "link"} title={pending?.kind === "link" ? `Password link for ${pending.person.name}` : "Password link"} confirmLabel="Copy link" onCancel={() => setPending(null)}
        onConfirm={async () => { if (pending?.kind === "link") { await navigator.clipboard?.writeText(pending.url).catch(() => undefined); setMsg({ text: "Link copied. Send it to them: it works once.", ok: true }); setPending(null); } }}>
        {pending?.kind === "link" && <><p>Send this to {pending.person.name}. It works once, for {pending.hours} hours.</p><p className="selectable small">{pending.url}</p></>}
      </ConfirmDialog>
    </>
  );
}
