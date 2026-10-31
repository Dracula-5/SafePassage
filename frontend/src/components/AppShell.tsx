"use client";

// The frame around every signed-in screen: brand, screen name, live status and account menu.

import Link from "next/link";
import { ReactNode } from "react";

import { Account } from "@/lib/api";
import { signOut } from "@/lib/session";

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg className="logo" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="#0b7285" />
      <path d="M8 23c5 0 4-7 8-7s3-7 8-7" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" />
      <circle cx="8" cy="23" r="2.6" fill="#ffd43b" />
      <circle cx="24" cy="9" r="2.6" fill="#fff" />
    </svg>
  );
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
    <div className="shell">
      <header className="topbar">
        <Link href="/" className="brand"><Logo /><span>ReliefRoute</span></Link>
        <span className="topbar-title">{title}</span>
        <span className="spacer" />
        {status}
        <div className="account">
          <span className="avatar" aria-hidden="true">{account.name.charAt(0).toUpperCase()}</span>
          <span className="account-text">
            <b>{account.name}</b>
            <small>{ROLE_NAMES[account.role]}</small>
          </span>
          <button className="ghost" onClick={signOut}>Sign out</button>
        </div>
      </header>
      {children}
    </div>
  );
}
