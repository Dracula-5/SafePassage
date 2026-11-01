"use client";

// Every active alert, searchable, with a drawer for the details. Alerts come from the live sources; drills are
// the duty officer's own what-if areas and are always labelled. Nothing here is invented.

import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { api, HazardProperties, PlanRoute, Severity } from "@/lib/api";
import { ago, sourceName } from "@/lib/format";

import { ActionButton, AlertCard, Column, DataTable, DetailDrawer, EmptyState, FilterBar, FilterChips, PageHeader, SearchInput, StatusBadge, Surface } from "../ui";
import { matches, SEVERITIES, words } from "./constants";
import styles from "./ops.module.css";
import { useOps } from "./OpsProvider";
import { EvacuateDialog, HazardFlags, SeverityBadge } from "./parts";

const LiveMap = dynamic(() => import("../LiveMap"), { ssr: false, loading: () => <div className="map map-loading">Loading map…</div> });
const NO_ROUTES: PlanRoute[] = [];
const MAX_ROWS = 100;
const RANK: Record<string, number> = { extreme: 0, severe: 1, moderate: 2, minor: 3, unknown: 4 };

type Hazard = HazardProperties;

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "Not given";
}

export default function HazardsPage() {
  const router = useRouter();
  const { hazards, vehicles, liveRoutes, bounds, reloadHazards } = useOps();
  const [query, setQuery] = useState("");
  const [severity, setSeverity] = useState<Severity | "all">("all");
  const [type, setType] = useState("all");
  const [source, setSource] = useState("all");
  const [view, setView] = useState<"list" | "map">("list");
  const [openId, setOpenId] = useState<string | null>(null);
  const [evacuateFor, setEvacuateFor] = useState<string | null>(null);
  const [note, setNote] = useState<{ text: string; ok: boolean } | null>(null);
  const [ending, setEnding] = useState(false);

  const all: Hazard[] = useMemo(() => (hazards?.features ?? []).map((f) => f.properties), [hazards]);
  const types = useMemo(() => [...new Set(all.map((h) => h.hazard_type))].sort(), [all]);
  const sources = useMemo(() => [...new Set(all.map((h) => h.source))].sort(), [all]);
  const shown = useMemo(() => all
    .filter((h) => (severity === "all" || h.severity === severity) && (type === "all" || h.hazard_type === type) && (source === "all" || h.source === source)
      && matches(query, h.headline, h.area_desc, h.description, h.hazard_type, sourceName(h.source)))
    .sort((a, b) => Number(b.is_drill) - Number(a.is_drill) || (RANK[a.severity] ?? 9) - (RANK[b.severity] ?? 9)), [all, severity, type, source, query]);
  const open = all.find((h) => h.id === openId) ?? null;
  const counts = (s: Severity) => all.filter((h) => h.severity === s).length;

  async function endDrill(id: string) {
    setEnding(true);
    try {
      await api.cancelDrill(id);
      setOpenId(null);
      reloadHazards();
    } catch (err) {
      setNote({ text: (err as Error).message, ok: false });
    } finally {
      setEnding(false);
    }
  }

  const columns: Column<Hazard>[] = [
    { key: "severity", header: "Severity", render: (h) => <SeverityBadge severity={h.severity} /> },
    {
      key: "hazard", header: "Hazard",
      render: (h) => (
        <span className={styles.cell}>
          <b className={styles.clip}>{h.headline}</b>
          <small className={styles.clip}>{h.area_desc ?? "Area not named"}</small>
          <HazardFlags hazard={h} />
        </span>
      ),
    },
    { key: "type", header: "Type", render: (h) => words(h.hazard_type) },
    { key: "source", header: "Source", render: (h) => <span className={styles.cell}><b>{sourceName(h.source)}</b><small>updated {ago(h.fetched_at)}</small></span> },
    { key: "expires", header: "Expires", render: (h) => <span className="small">{h.expires_at ? new Date(h.expires_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "Not given"}</span> },
  ];

  return (
    <>
      <PageHeader title="Hazards & Alerts" subtitle="Live alerts from the official sources, plus any what-if drills you have drawn."
        actions={<FilterChips label="View" value={view} onChange={setView} options={[{ key: "list", label: "List" }, { key: "map", label: "Map" }]} />} />
      {note && <AlertCard tone={note.ok ? "good" : "bad"}>{note.text}</AlertCard>}

      <FilterBar>
        <SearchInput value={query} onChange={setQuery} placeholder="Search by place, headline or type" />
        <FilterChips label="Severity" value={severity} onChange={setSeverity}
          options={[{ key: "all", label: "All", count: all.length }, ...SEVERITIES.map((s) => ({ key: s, label: words(s), count: counts(s) }))]} />
        <select aria-label="Hazard type" value={type} onChange={(e) => setType(e.target.value)} style={{ width: "auto", minWidth: 150 }}>
          <option value="all">All types</option>
          {types.map((t) => <option key={t} value={t}>{words(t)}</option>)}
        </select>
        <select aria-label="Source" value={source} onChange={(e) => setSource(e.target.value)} style={{ width: "auto", minWidth: 170 }}>
          <option value="all">All sources</option>
          {sources.map((s) => <option key={s} value={s}>{sourceName(s)}</option>)}
        </select>
      </FilterBar>

      {hazards === null ? <Surface><p className={styles.muted}>Loading alerts…</p></Surface> : view === "map" ? (
        <div className={styles.stage}>
          <LiveMap hazards={hazards} initialBounds={bounds} drawing={false} draftVertices={[]} onMapClick={() => undefined} onCancelDrill={endDrill}
            onFacilitiesStatus={() => undefined} onFindDrivers={(id) => router.push(`/dashboard/fleet?place=${id}`)} onAskTruck={(id) => router.push(`/dashboard/fleet?truck=${id}`)}
            onEvacuate={setEvacuateFor} vehicles={vehicles} routes={NO_ROUTES} liveRoutes={liveRoutes} />
        </div>
      ) : (
        <Surface flush>
          {shown.length > MAX_ROWS && <p className={styles.muted} style={{ padding: "14px 20px 0", fontSize: 13 }}>Showing the first {MAX_ROWS} of {shown.length}. Search or filter to narrow the list.</p>}
          <DataTable caption="Active hazards" columns={columns} rows={shown.slice(0, MAX_ROWS)} rowKey={(h) => h.id} onRowClick={(h) => setOpenId(h.id)} selectedKey={openId}
            empty={<EmptyState icon="hazard" title={all.length === 0 ? "No active hazards" : "No hazards match"}>{all.length === 0 ? "When a source reports an alert, or you draw a drill, it appears here." : "Try a different search or clear a filter."}</EmptyState>} />
        </Surface>
      )}

      <DetailDrawer open={open !== null} title="Hazard details" onClose={() => setOpenId(null)}>
        {open && (
          <>
            <div className={styles.stackTight}>
              <span className={styles.tags}><SeverityBadge severity={open.severity} /><StatusBadge tone="info">{words(open.hazard_type)}</StatusBadge><HazardFlags hazard={open} /></span>
              <h2 style={{ fontSize: 19 }}>{open.headline}</h2>
              {open.description && <p className={styles.muted}>{open.description}</p>}
            </div>
            <dl className={styles.facts}>
              <div className={styles.fact}><small>Area</small><b>{open.area_desc ?? "Not named"}</b></div>
              <div className={styles.fact}><small>Source</small><b>{sourceName(open.source)}</b></div>
              <div className={styles.fact}><small>Effective</small><b>{when(open.effective_at)}</b></div>
              <div className={styles.fact}><small>Expires</small><b>{when(open.expires_at)}</b></div>
              <div className={styles.fact}><small>Last updated</small><b>{ago(open.fetched_at)}</b></div>
              <div className={styles.fact}><small>Version</small><b>{open.version}</b></div>
              <div className={styles.fact}><small>Reference</small><b>{open.source_ref}</b></div>
              <div className={styles.fact}><small>Status</small><b>{open.status}</b></div>
            </dl>
            {open.is_stale && <AlertCard tone="bad" title="This data is stale">The source has not reported on time, so this alert may have changed.</AlertCard>}
            <div className="row">
              <ActionButton variant="primary" icon="truck" onClick={() => router.push(`/dashboard/fleet?place=${open.id}`)}>Find nearest drivers</ActionButton>
              <ActionButton icon="evac" onClick={() => setEvacuateFor(open.id)}>Request evacuation</ActionButton>
              {open.is_drill && <ActionButton variant="danger" busy={ending} onClick={() => endDrill(open.id)}>End drill</ActionButton>}
            </div>
          </>
        )}
      </DetailDrawer>
      <EvacuateDialog hazardId={evacuateFor} label={all.find((h) => h.id === evacuateFor)?.headline.slice(0, 60)} onClose={() => setEvacuateFor(null)} onResult={(text, ok) => setNote({ text, ok })} />
    </>
  );
}
