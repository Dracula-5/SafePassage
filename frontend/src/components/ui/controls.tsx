"use client";

// A button that says what it does, shows that it is working, and cannot be pressed twice.

import { ButtonHTMLAttributes, ReactNode } from "react";

import Icon from "./icons";

export function ActionButton({ variant = "secondary", icon, busy, busyLabel, children, ...rest }: {
  variant?: "primary" | "secondary" | "danger";
  icon?: string;
  busy?: boolean;
  busyLabel?: string;
  children: ReactNode;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  const cls = variant === "primary" ? "primary" : variant === "danger" ? "danger" : "";
  return (
    <button type="button" {...rest} className={`${cls} ${rest.className ?? ""}`} disabled={busy || rest.disabled}>
      {icon && !busy && <Icon name={icon} size={18} />}
      {busy ? (busyLabel ?? "Working…") : children}
    </button>
  );
}
