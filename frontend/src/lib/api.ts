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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (!API_BASE) throw new Error("NEXT_PUBLIC_API_BASE is not set");
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store", ...init });
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
