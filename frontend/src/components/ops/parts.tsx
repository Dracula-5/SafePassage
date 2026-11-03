"use client";

// Small pieces several duty-officer pages share: how a plan, a hazard or an evacuation is labelled, and the evacuation request dialog.

import { useState } from "react";

import { Evacuation, HazardProperties, plansApi, PlanSummary } from "@/lib/api";

import { ConfirmDialog, StatusBadge, Tone } from "../ui";
import { SEVERITY_COLORS } from "./constants";
import styles from "./ops.module.css";

const PLAN_STATUS: Record<string, { text: string; tone: Tone }> = {
  pending: { text: "Starting", tone: "plain" },
  planning: { text: "Planning", tone: "info" },
  awaiting_approval: { text: "Awaiting approval", tone: "warn" },
  approved: { text: "Approved", tone: "good" },
  dispatched: { text: "Dispatched", tone: "good" },
  rejected: { text: "Rejected", tone: "bad" },
  failed: { text: "Failed", tone: "bad" },
  unreachable: { text: "Unreachable", tone: "bad" },
  superseded: { text: "Replaced", tone: "plain" },
};

export function planLabel(status: string): { text: string; tone: Tone } {
  return PLAN_STATUS[status] ?? { text: status.replace(/_/g, " "), tone: "plain" };
}

/** What to say when a plan has no summary: a plan that ended must not read as if it were still being prepared. */
const NO_SUMMARY: Record<string, string> = {
  failed: "This plan could not be completed. Open it to see why.",
  unreachable: "No safe road route was found. Open it to see why.",
  superseded: "Replaced by a newer plan.",
  rejected: "Rejected by a duty officer.",
};

export function planText(plan: PlanSummary, max?: number): string {
  return (plan.summary ?? NO_SUMMARY[plan.status] ?? "The plan is still being prepared.").slice(0, max);
}

export function PlanStatus({ status }: { status: string }) {
  const { text, tone } = planLabel(status);
  return <StatusBadge tone={tone}>{text}</StatusBadge>;
}

export function SeverityBadge({ severity }: { severity: HazardProperties["severity"] }) {
  const tone: Tone = severity === "extreme" || severity === "severe" ? "bad" : severity === "moderate" ? "warn" : "info";
  return (
    <StatusBadge tone={tone}>
      <span className={styles.swatch} style={{ background: SEVERITY_COLORS[severity] }} />
      {severity.charAt(0).toUpperCase() + severity.slice(1)}
    </StatusBadge>
  );
}

/** The map's warehouse icon, for legends: the same shape LiveMap draws (warehouseIcon there). */
export function WarehouseGlyph({ color, size = 14 }: { color: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect x="2" y="2" width="28" height="28" rx="7" fill={color} stroke="#fff" strokeWidth="3" />
      <path d="M8 15 16 8 24 15Z" fill="#fff" />
      <rect x="10" y="15" width="12" height="9" fill="#fff" />
      <rect x="14" y="18" width="4" height="6" fill={color} />
    </svg>
  );
}

/** DRILL and STALE are always shown beside a hazard: a person must never mistake one for the other. */
export function HazardFlags({ hazard }: { hazard: HazardProperties }) {
  return (
    <span className={styles.tags}>
      {hazard.is_drill && <StatusBadge tone="drill">DRILL</StatusBadge>}
      {hazard.is_stale && <StatusBadge tone="bad">Data stale</StatusBadge>}
    </span>
  );
}

export const EVACUATION_STAGES: Record<Evacuation["stage"], { text: string; tone: Tone; step: number }> = {
  waiting: { text: "Waiting for a truck", tone: "warn", step: 1 },
  on_the_way: { text: "Truck on the way", tone: "info", step: 2 },
  on_board: { text: "People on board", tone: "info", step: 3 },
  safe: { text: "Reached safety", tone: "good", step: 4 },
  failed: { text: "Needs a person", tone: "bad", step: 0 },
};

/** Asks how many people need to leave a zone, then asks the backend to plan the evacuation. */
export function EvacuateDialog({ hazardId, label, choices, onChoose, onClose, onResult }: {
  hazardId: string | null;
  label?: string;
  /** When there is a choice of zone, it is made inside the dialog. */
  choices?: { id: string; label: string }[];
  onChoose?: (id: string) => void;
  onClose: () => void;
  onResult: (message: string, ok: boolean) => void;
}) {
  const [people, setPeople] = useState("40");
  const [busy, setBusy] = useState(false);
  const count = Number(people);
  const valid = Number.isInteger(count) && count > 0;

  async function submit() {
    if (!hazardId || !valid) return;
    setBusy(true);
    try {
      await plansApi.evacuate(hazardId, count);
      onResult("Evacuation requested. The nearest free truck is on its way: see Evacuations.", true);
    } catch (err) {
      onResult((err as Error).message, false);
    } finally {
      setBusy(false);
      onClose();
    }
  }

  return (
    <ConfirmDialog open={hazardId !== null} title="Request an evacuation" confirmLabel="Request evacuation" busy={busy} onCancel={onClose} onConfirm={submit}>
      {choices && choices.length > 1 && onChoose ? (
        <label style={{ display: "grid", gap: 6, fontWeight: 600, fontSize: 14 }}>
          Zone
          <select value={hazardId ?? ""} onChange={(e) => onChoose(e.target.value)}>{choices.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}</select>
        </label>
      ) : null}
      <p>{label && !(choices && choices.length > 1) ? `${label}: ` : ""}How many people need to leave? The nearest free truck collects them just outside the zone and takes them to a safe point.</p>
      <label style={{ display: "grid", gap: 6, fontWeight: 600, fontSize: 14 }}>
        People
        <input type="number" min={1} step={1} inputMode="numeric" value={people} onChange={(e) => setPeople(e.target.value)} aria-invalid={!valid} />
      </label>
      {!valid && <small className={styles.muted}>Enter a whole number of people, at least 1.</small>}
    </ConfirmDialog>
  );
}
