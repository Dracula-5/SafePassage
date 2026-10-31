"use client";

// Everyone with an account: vouch for suppliers, give a person a password reset link, or remove access.

import { useCallback, useEffect, useState } from "react";

import { Account, teamApi, TeamMember } from "@/lib/api";
import { ago } from "@/lib/format";

import { REFRESH_MS } from "./LiveStatus";

const ROLE_NAMES = { operator: "Duty officer", driver: "Driver", supplier: "Supplier" };

export default function TeamPanel({ me }: { me: Account }) {
  const [team, setTeam] = useState<TeamMember[] | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(() => teamApi.list().then(setTeam, (err) => setMsg(err.message)), []);

  useEffect(() => {
    const first = setTimeout(load, 0);
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [load]);

  async function act(action: Promise<unknown>) {
    await action.then(load, (err) => setMsg(err.message));
  }

  async function resetLink(person: TeamMember) {
    try {
      const { path, expires_in_hours } = await teamApi.resetLink(person.id);
      setMsg(`Send this to ${person.name}. It works once, for ${expires_in_hours} hours: ${window.location.origin}${path}`);
    } catch (err) {
      setMsg((err as Error).message);
    }
  }

  return (
    <>
      {msg && <div className="alert small selectable">{msg}</div>}
      <ul className="list">
        {(team ?? []).map((p) => (
          <li key={p.id} className={p.disabled ? "person disabled" : "person"}>
            <b>{p.name}</b>{p.id === me.id && <small className="muted"> (you)</small>}
            <small className="block muted">{p.email}{p.email_verified ? " · confirmed" : ""}</small>
            <small className="block muted">
              {ROLE_NAMES[p.role]}{p.vehicle_label ? ` · ${p.vehicle_label}` : ""}{p.supplier_name ? ` · ${p.supplier_name}` : ""} · seen {ago(p.last_signin_at)}
            </small>
            <div className="row">
              {p.supplier_id && (
                <button className="link" onClick={() => act(teamApi.verifySupplier(p.supplier_id!, !p.supplier_verified))}>
                  {p.supplier_verified ? "Withdraw verification" : "Verify supplier"}
                </button>
              )}
              <button className="link" onClick={() => resetLink(p)}>Password link</button>
              {p.id !== me.id && (
                <button className="link danger" onClick={() => act(teamApi.setDisabled(p.id, !p.disabled))}>{p.disabled ? "Restore" : "Remove access"}</button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}
