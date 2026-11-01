// Display helpers shared by the screens.

export function ago(iso: string | Date | null): string {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 90) return `${s}s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 172800) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

/** A simulated truck's number plate. The screens show a SIMULATED badge beside it instead of the long label. */
export function plate(label: string): string {
  return label.replace(/^TEST-SIM-GPS\s+/, "");
}

export function tonnes(kg: number): string {
  return kg >= 1000 ? `${(kg / 1000).toFixed(1)} t` : `${Math.round(kg)} kg`;
}

/** "RICE_KG" reads as "Rice"; the unit is shown beside it. */
export function itemName(code: string): string {
  const word = code.split("_")[0].toLowerCase();
  const names: Record<string, string> = { rte: "Ready-to-eat meals", ors: "ORS sachets", hygiene: "Hygiene kits", medical: "Medical kits" };
  return names[word] ?? word.charAt(0).toUpperCase() + word.slice(1);
}

export function sourceName(source: string): string {
  const names: Record<string, string> = {
    aepds: "AePDS ration stock", gdacs: "GDACS global alerts", open_meteo: "Open-Meteo forecast", osm_extract: "OpenStreetMap places",
    overpass: "OpenStreetMap facilities", sachet: "SACHET (NDMA alerts)", firms: "NASA FIRMS fires", brain_worker: "Planning worker",
    ingestion_worker: "Data worker",
  };
  return names[source] ?? source;
}
