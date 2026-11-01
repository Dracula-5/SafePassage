// Colours and lists the operator screens and the map share.

import { Severity } from "@/lib/api";

export const SEVERITY_COLORS: Record<string, string> = {
  extreme: "#b3002d",
  severe: "#e8590c",
  moderate: "#f2b705",
  minor: "#4c9be8",
  unknown: "#868e96",
};
export const SIMULATED_TRUCK = "#12263a";  // the ring on a white truck: a real truck's is teal
export const SUPPLY_ROUTE = "#f76707";
export const EVACUATION_ROUTE = "#2b8a3e";

export const HAZARD_TYPES = ["flood", "cyclone", "heavy_rain", "storm", "landslide", "wildfire", "heatwave", "other"];
export const SEVERITIES: Severity[] = ["extreme", "severe", "moderate", "minor"];

export function away(metres: number | undefined): string {
  if (metres === undefined) return "";
  return metres === 0 ? "inside the area" : `${(metres / 1000).toFixed(metres < 10_000 ? 1 : 0)} km away`;
}

export function matches(query: string, ...fields: (string | null | undefined)[]): boolean {
  const wanted = query.trim().toLowerCase();
  return !wanted || fields.some((f) => f?.toLowerCase().includes(wanted));
}

/** "heavy_rain" reads as "Heavy rain". */
export function words(text: string): string {
  const spaced = text.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
