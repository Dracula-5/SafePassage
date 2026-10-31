// Typed client for the ReliefRoute backend (contracts/openapi.yaml). No data is generated here:
// every value shown in the UI comes from these calls.

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

export type Severity = "minor" | "moderate" | "severe" | "extreme" | "unknown";

export interface HazardProperties {
  id: string;
  source: string;
  source_ref: string;
  hazard_type: string;
  severity: Severity;
  status: string;
  headline: string;
  description: string | null;
  area_desc: string | null;
  effective_at: string | null;
  expires_at: string | null;
  fetched_at: string;
  version: number;
  is_drill: boolean;
  is_stale: boolean;
}

export interface FacilityProperties {
  id: string;
  kind: string;
  name: string;
  source: string;
  source_ref: string;
  capacity_people: number | null;
  is_supplier: boolean;
}

export interface FeatureCollection<P> {
  type: "FeatureCollection";
  features: { type: "Feature"; id: string; geometry: GeoJSON.Geometry; properties: P }[];
}

export interface SourceHealth {
  source: string;
  breaker_state: "closed" | "open" | "half_open";
  last_success_at: string | null;
  last_failure_at: string | null;
  last_error: string | null;
  ttl_seconds: number;
  fallback_source: string | null;
  is_stale: boolean;
}

export interface DrillInput {
  geometry: GeoJSON.Polygon;
  hazard_type: string;
  severity: Severity;
  label: string;
  ttl_minutes: number;
}

const TOKEN_KEY = "rr_operator_token";

function operatorToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function forgetToken(): void {
  try {
    sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // Storage is blocked: nothing was kept.
  }
}

let declined = false; // Cancelled once: the public map stays usable, and polling must not ask again.

/** Asks for the operator token when a protected endpoint refuses. Pressing a button asks again. */
function askForToken(method: string): boolean {
  if (declined && method === "GET") return false;
  const token = window.prompt("Operator token");
  declined = !token;
  if (!token) return false;
  try {
    sessionStorage.setItem(TOKEN_KEY, token);
  } catch {
    return false;
  }
  return true;
}

async function request<T>(path: string, init?: RequestInit, retried = false): Promise<T> {
  if (!API_BASE) throw new Error("NEXT_PUBLIC_API_BASE is not set");
  const token = operatorToken();
  const headers = { ...init?.headers, ...(token ? { "X-Operator-Token": token } : {}) };
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store", ...init, headers });
  if (res.status === 401 && !retried) {
    // Several calls fail together on first load: whichever comes second finds the token already there.
    if (operatorToken() !== token || askForToken(init?.method ?? "GET")) return request<T>(path, init, true);
  } else if (res.status === 401) {
    forgetToken(); // Refused even with a token: wrong one. Asking again on every poll would help nobody.
    declined = true;
  }
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${init?.method ?? "GET"} ${path} failed: HTTP ${res.status} ${body.slice(0, 200)}`);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const api = {
  hazards: () => request<FeatureCollection<HazardProperties>>("/api/hazards"),
  facilities: (bbox: [number, number, number, number], kinds: string[]) =>
    request<FeatureCollection<FacilityProperties>>(
      `/api/facilities?bbox=${bbox.map((v) => v.toFixed(4)).join(",")}&kind=${kinds.join(",")}`,
    ),
  sourceHealth: () => request<SourceHealth[]>("/api/sources/health"),
  createDrill: (input: DrillInput) =>
    request<GeoJSON.Feature>("/api/drill", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  cancelDrill: (id: string) => request<void>(`/api/drill/${id}`, { method: "DELETE" }),
};

/** Region bbox for the initial view, from NEXT_PUBLIC_REGION_BBOX (min_lon,min_lat,max_lon,max_lat). */
export function regionBbox(): [number, number, number, number] | null {
  const raw = process.env.NEXT_PUBLIC_REGION_BBOX;
  if (!raw) return null;
  const parts = raw.split(",").map(Number);
  return parts.length === 4 && parts.every(Number.isFinite) ? (parts as [number, number, number, number]) : null;
}

// ---- Gate 2: plans (served by A), vehicles, live stream (served by B)

export interface PlanSummary {
  id: string;
  parent_plan_id: string | null;
  status: string;
  trigger_kind: string;
  hazard_ids: string[];
  is_drill: boolean;
  requires_approval: boolean;
  summary: string | null;
  created_at: string;
  updated_at: string;
}

export interface PlanRoute {
  id: string;
  vehicle_id: string;
  status: string;
  geometry: GeoJSON.LineString;
  distance_m: number;
  duration_s: number;
  load_kg: number;
  min_hazard_clearance_m: number | null;
  stops: { facility_id: string; name: string; kind: string; eta: string; order_keys: string[] }[];
  completed_stops: number;
}

export type Escalation =
  | { destination_facility_id: string; item_code: string; quantity_short: number; reason: string }
  | { kind: "alternate_hub"; facility_id: string; name: string; lon: number; lat: number; reason: string };

export interface PlanDetail extends PlanSummary {
  demand: { destination_facility_id: string; item_code: string; quantity: number; basis: string }[];
  routes: PlanRoute[];
  orders: { id: string; supplier_id: string; status: string; idempotency_key: string; lines: { item_code: string; quantity: number }[] }[];
  critic: { attempt: number; passed: boolean; violations: string[]; created_at: string }[];
  approval: { decision: string; decided_by: string; reason: string | null; decided_at: string } | null;
  unreachable_reason: string | null;
  escalations: Escalation[];
  triage: { priority: string; planning_horizon_hours: number; rationale: string; key_risks: string[] } | null;
}

export interface Vehicle {
  id: string;
  label: string;
  driver_name: string | null;
  capacity_kg: number;
  status: string;
  position: GeoJSON.Point | null;
  last_seen_at: string | null;
  is_offline: boolean;
}

export interface AgentEvent {
  id: number;
  plan_id: string | null;
  node: string;
  event_type: string;
  message: string;
  payload: Record<string, unknown>;
  source_ids: string[];
  created_at: string;
}

export const plansApi = {
  list: () => request<PlanSummary[]>("/api/plans?limit=20"),
  get: (id: string) => request<PlanDetail>(`/api/plans/${id}`),
  decide: (id: string, decision: "approved" | "rejected", decided_by: string, reason?: string) =>
    request<unknown>(`/api/plans/${id}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, decided_by, reason: reason || undefined }),
    }),
  vehicles: () => request<Vehicle[]>("/api/vehicles"),
};

/** EventSource cannot send the token, so each connection gets a short-lived read-only ticket. */
export async function streamUrl(): Promise<string> {
  if (declined) throw new Error("no operator token"); // This retries in the background, so it must not ask.
  const { ticket } = await request<{ ticket: string | null }>("/api/stream/ticket", { method: "POST" });
  return `${API_BASE}/api/stream${ticket ? `?ticket=${encodeURIComponent(ticket)}` : ""}`;
}
