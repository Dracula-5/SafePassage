"use client";

// A plain frame for screens that do not have their own navigation yet: brand, who is signed in, and Sign out.
// The duty-officer, driver and supplier apps each have a full layout of their own.

import Link from "next/link";
import { ReactNode } from "react";

import { Account } from "@/lib/api";
import { signOut } from "@/lib/session";

import { BrandMark } from "./ui/icons";

export function Logo({ size = 28 }: { size?: number }) {
  return <BrandMark size={size} />;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="loading-screen" role="status">
      <span className="spinner" />
      {label}
    </div>
  );
}

const ROLE_NAMES = { operator: "Duty officer", driver: "Driver", supplier: "Supplier" };

export default function AppShell({ account, title, status, children }: {
  account: Account;
  title: string;
  status?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div style={{ minHeight: "100dvh" }}>
      <header className="shell-header" style={{ position: "sticky", top: 0, zIndex: 20, display: "flex", alignItems: "center", gap: 14, minHeight: 60, padding: "8px 20px", background: "#fff", borderBottom: "1px solid var(--line)" }}>
        <Link href="/" style={{ display: "inline-flex", alignItems: "center", gap: 10, color: "var(--ink)", textDecoration: "none", fontWeight: 700, fontSize: 18 }}>
          <Logo size={30} /><span>SafePassage</span>
        </Link>
        <span className="muted shell-title">{title}</span>
        <span className="spacer" />
        {status}
        <span className="muted small shell-who"><b style={{ color: "var(--ink)" }}>{account.name}</b> · {ROLE_NAMES[account.role]}</span>
        <button type="button" onClick={signOut}>Sign out</button>
      </header>
      {children}
    </div>
  );
}
