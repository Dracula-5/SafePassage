// Typed client for the ReliefRoute backend (contracts/openapi.yaml). No data is generated here: every
// value shown in the UI comes from these calls. The API shares this site's address, so the session
// cookie goes along by itself and no secret ever reaches browser code.

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function send<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { cache: "no-store", ...init });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(res.status, body?.detail ?? `The server answered ${res.status}`);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

/** For signed-in screens: an expired session goes back to sign-in instead of showing a broken page. */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  try {
    return await send<T>(path, init);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- outside React, and a full reload clears state
      window.location.href = `/signin?next=${encodeURIComponent(window.location.pathname)}`;
    }
    throw err;
  }
}

function post<T>(path: string, body?: unknown, headers?: Record<string, string>): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

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

export interface PublicStatus {
  hazards_active: number;
  hazards_severe: number;
  sources_fresh: number;
  sources_total: number;
  hazards_changed: string;
}

export interface Overview {
  plans_awaiting: number;
  routes_live: number;
  kg_on_routes: number;
  orders_open: number;
  orders_delivered: number;
  trucks_online: number;
  trucks_total: number;
  people_safe: number;
  people_waiting: number;
}

export const api = {
  status: () => send<PublicStatus>("/api/status"),
  hazards: () => request<FeatureCollection<HazardProperties>>("/api/hazards"),
  facilities: (bbox: [number, number, number, number], kinds: string[]) =>
    request<FeatureCollection<FacilityProperties>>(
      `/api/facilities?bbox=${bbox.map((v) => v.toFixed(4)).join(",")}&kind=${kinds.join(",")}`,
    ),
  sourceHealth: () => request<SourceHealth[]>("/api/sources/health"),
  overview: () => request<Overview>("/api/overview"),
  createDrill: (input: DrillInput) => post<GeoJSON.Feature>("/api/drill", input),
  cancelDrill: (id: string) => request<void>(`/api/drill/${id}`, { method: "DELETE" }),
};

/** Region bbox for the initial view, from NEXT_PUBLIC_REGION_BBOX (min_lon,min_lat,max_lon,max_lat). */
export function regionBbox(): [number, number, number, number] | null {
  const raw = process.env.NEXT_PUBLIC_REGION_BBOX;
  if (!raw) return null;
  const parts = raw.split(",").map(Number);
  return parts.length === 4 && parts.every(Number.isFinite) ? (parts as [number, number, number, number]) : null;
}

// ---- Accounts

export type Role = "operator" | "driver" | "supplier";

export interface Account {
  id: string;
  email: string;
  name: string;
  role: Role;
}

export interface TeamMember extends Account {
  last_signin_at: string | null;
  disabled: boolean;
  supplier_id: string | null;
  supplier_name: string | null;
  supplier_verified: boolean | null;
  vehicle_label: string | null;
  email_verified: boolean;
}

const json = { "Content-Type": "application/json" };

export const authApi = {
  me: () => send<Account>("/api/auth/me"),
  signIn: (email: string, password: string) =>
    send<Account>("/api/auth/signin", { method: "POST", headers: json, body: JSON.stringify({ email, password }) }),
  signUp: (input: { email: string; password: string; name: string; role: Role; code?: string }) =>
    send<Account>("/api/auth/signup", { method: "POST", headers: json, body: JSON.stringify(input) }),
  signOut: () => send<void>("/api/auth/signout", { method: "POST" }),
  reset: (token: string, password: string) =>
    send<void>("/api/auth/reset", { method: "POST", headers: json, body: JSON.stringify({ token, password }) }),
  options: () => send<{ email: boolean }>("/api/auth/options"),
  forgot: (email: string) => send<unknown>("/api/auth/forgot", { method: "POST", headers: json, body: JSON.stringify({ email }) }),
  verify: (token: string) => send<void>("/api/auth/verify", { method: "POST", headers: json, body: JSON.stringify({ token }) }),
};

export interface SupplierListing {
  id: string;
  name: string;
  site: string;
  verified: boolean;
  has_account: boolean;
  contact: string | null;
  stock: { item_code: string; available: number; reserved: number }[];
  reported_at: string | null;
}

export const teamApi = {
  list: () => request<TeamMember[]>("/api/accounts"),
  suppliers: () => request<SupplierListing[]>("/api/suppliers"),
  setDisabled: (id: string, disabled: boolean) => post<unknown>(`/api/accounts/${id}/access`, { disabled }),
  resetLink: (id: string) => post<{ path: string; expires_in_hours: number }>(`/api/accounts/${id}/reset-link`),
  verifySupplier: (id: string, verified: boolean) => post<unknown>(`/api/suppliers/${id}/verify`, { verified }),
};

// ---- Plans (served by A), vehicles, live stream (served by B)

export interface PlanSummary {
  id: string;
  parent_plan_id: string | null;
  status: string;
  trigger_kind: string;
  hazard_ids: string[];
  is_drill: boolean;
  requires_approval: boolean;
  summary: string | null;
  evacuation: boolean;
  created_at: string;
  updated_at: string;
}

export interface LiveRoute {
  id: string;
  vehicle_id: string;
  kind: "supply" | "evacuation";
  geometry: GeoJSON.LineString;
}

export interface Evacuation {
  id: string;
  zone: string;
  is_drill: boolean;
  settlement: string;
  people: number;
  simulated: boolean;
  stage: "waiting" | "on_the_way" | "on_board" | "safe" | "failed";
  failure: string | null;
  pickup_name: string | null;
  safe_name: string | null;
  zones_avoided: number | null;
  zones_crossed: number | null;
  vehicle_id: string | null;
  plan_id: string | null;
  created_at: string;
}

export interface PlanRoute {
  id: string;
  vehicle_id: string;
  status: string;
  kind: "supply" | "evacuation";
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
  on_route: boolean;
  simulated: boolean;
  fleet_number: number;
}

export interface DriverRequest {
  id: string;
  vehicle_id: string;
  hazard_id: string;
  place: string;
  status: "sent" | "accepted" | "declined" | "withdrawn";
  requested_by: string;
  created_at: string;
  answered_at: string | null;
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
  list: () => request<PlanSummary[]>("/api/plans?limit=30"),
  get: (id: string) => request<PlanDetail>(`/api/plans/${id}`),
  decide: (id: string, decision: "approved" | "rejected", decided_by: string, reason?: string) =>
    post<unknown>(`/api/plans/${id}/approve`, { decision, decided_by, reason: reason || undefined }),
  planAgain: (hazard_ids: string[], requested_by: string) =>
    post<PlanSummary>("/api/plans/trigger", { hazard_ids, requested_by, reason: "Planned again by a duty officer" }),
  liveRoutes: () => request<LiveRoute[]>("/api/routes/live"),
  evacuations: () => request<Evacuation[]>("/api/evacuations"),
  evacuate: (hazard_id: string, people: number) => post<unknown>("/api/evacuations", { hazard_id, people }),
  vehicles: () => request<Vehicle[]>("/api/vehicles"),
  revokeVehicle: (id: string) => post<unknown>(`/api/vehicles/${id}/revoke`),
};

export const requestsApi = {
  list: () => request<DriverRequest[]>("/api/driver-requests"),
  send: (vehicle_id: string, hazard_id: string) => post<DriverRequest>("/api/driver-requests", { vehicle_id, hazard_id }),
  withdraw: (id: string) => request<void>(`/api/driver-requests/${id}`, { method: "DELETE" }),
  distances: (hazardId: string) => request<Record<string, number>>(`/api/hazards/${hazardId}/distances`),
};

export const STREAM_URL = "/api/stream";

// ---- Driver screen. Telemetry uses the device token, so GPS keeps flowing after a session expires and
// a dead zone never turns into a redirect.

export interface Assignment {
  plan_id: string;
  is_drill: boolean;
  route: PlanRoute;
  instructions: { language: string; text: string };
}

export interface DeviceRegistration {
  vehicle: Vehicle;
  device_token: string;
}

export const driverApi = {
  vehicle: () => send<Vehicle>("/api/driver/vehicle"),
  register: (input: { label: string; driver_phone?: string; driver_language: string; capacity_kg: number }) =>
    post<DeviceRegistration>("/api/driver/vehicle", input),
  useThisDevice: () => post<DeviceRegistration>("/api/driver/vehicle/device"),
  // Without a token (a simulated truck, or a duty officer watching) the session cookie is what counts.
  assignment: (vehicleId: string, token: string) =>
    send<Assignment>(`/api/vehicles/${vehicleId}/assignment`, token ? { headers: { "X-Device-Token": token } } : undefined),
  request: (vehicleId: string, token: string) =>
    send<DriverRequest | null>(`/api/vehicles/${vehicleId}/request`, token ? { headers: { "X-Device-Token": token } } : undefined),
  answer: (vehicleId: string, token: string, accept: boolean) =>
    send<unknown>(`/api/vehicles/${vehicleId}/request/answer`, {
      method: "POST",
      headers: { ...json, ...(token ? { "X-Device-Token": token } : {}) },
      body: JSON.stringify({ accept }),
    }),
  telemetry: (vehicleId: string, token: string, fixes: unknown[]) =>
    send<{ accepted: number; duplicates: number }>("/api/telemetry", {
      method: "POST",
      headers: { ...json, "X-Device-Token": token },
      body: JSON.stringify({ vehicle_id: vehicleId, fixes }),
    }),
};

// ---- Presentation mode: simulated drivers, marked as simulated on every screen

export interface DemoStatus {
  enabled: boolean;
  trucks?: number;
  towns?: { name: string }[];
}

export type DriverMode = "normal" | "gps_lost" | "off_route";

export const demoApi = {
  status: () => send<DemoStatus>("/api/demo"),
  addDriver: (input: { driver_name: string; plate: string; capacity_kg: number; driver_language: string; town: string }) =>
    post<Vehicle>("/api/demo/drivers", input),
  setMode: (id: string, mode: DriverMode) => post<unknown>(`/api/demo/drivers/${id}/mode`, { mode }),
  simulateMyTruck: (input: { town: string; capacity_kg: number }) => post<Vehicle>("/api/demo/my-truck", input),
};

// ---- Supplier screen

export interface SupplierOrder {
  id: string;
  status: string;
  is_drill: boolean;
  destination: string;
  lines: { item_code: string; quantity: number }[];
  created_at: string;
  delivered_at: string | null;
}

export interface StockLevel {
  item_code: string;
  quantity: number;
  reserved: number;
  available: number;
  unit_weight_kg: number;
  reported_at: string;
}

export interface SupplierProfile {
  id: string;
  name: string;
  site: string;
  verified: boolean;
  stock: StockLevel[];
  items: { code: string; unit: string }[];
}

export interface FacilityMatch {
  id: string;
  name: string;
  kind: string;
  distance_m: number | null;
}

export const supplierApi = {
  me: () => send<SupplierProfile>("/api/supplier/me"),
  orders: () => send<SupplierOrder[]>("/api/supplier/orders"),
  register: (input: Record<string, unknown>) => post<unknown>("/api/supplier/me", input),
  report: (lines: { item_code: string; quantity: number; unit_weight_kg: number }[]) =>
    post<StockLevel[]>("/api/supplier/stock", { lines }),
  search: (q: string, near: [number, number] | null) => {
    const params = new URLSearchParams({ q });
    if (near) {
      params.set("lon", String(near[0]));
      params.set("lat", String(near[1]));
    }
    return request<FacilityMatch[]>(`/portal/api/facilities?${params}`);
  },
};
