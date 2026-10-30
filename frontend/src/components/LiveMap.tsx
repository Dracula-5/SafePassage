"use client";

// MapLibre map: live hazard polygons, facilities in view, and a polygon drawing mode for drills.
// All features come from the backend; the map only renders them.

import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource, LngLatBoundsLike, Map as MlMap, MapMouseEvent } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";

import { api, FacilityProperties, FeatureCollection, HazardProperties, PlanRoute, Vehicle } from "@/lib/api";

export const SEVERITY_COLORS: Record<string, string> = {
  extreme: "#b3002d",
  severe: "#e8590c",
  moderate: "#f2b705",
  minor: "#4c9be8",
  unknown: "#868e96",
};

const FACILITY_KINDS = ["hospital", "shelter", "warehouse", "godown", "school", "depot"];
const FACILITY_MIN_ZOOM = 9; // below this, tens of thousands of points would be requested

// Worker file served from public/ by scripts/copy-maplibre-worker.mjs (postinstall).
const WORKER_PATH = "/maplibre/maplibre-gl-worker.mjs";

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

interface Props {
  hazards: FeatureCollection<HazardProperties> | null;
  initialBounds: [number, number, number, number] | null;
  drawing: boolean;
  draftVertices: [number, number][];
  onMapClick: (lngLat: [number, number]) => void;
  onCancelDrill: (id: string) => void;
  onFacilitiesStatus: (status: string) => void;
  vehicles: Vehicle[];
  routes: PlanRoute[];
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

function hazardPopup(p: HazardProperties): string {
  const expires = p.expires_at ? new Date(p.expires_at).toLocaleString() : "not given";
  return `<div class="popup">
    ${p.is_drill ? '<span class="badge drill">DRILL</span>' : ""}
    ${p.is_stale ? '<span class="badge stale">DATA STALE</span>' : ""}
    <strong>${escapeHtml(p.headline)}</strong>
    <div>${escapeHtml(p.hazard_type)} &middot; <b>${escapeHtml(p.severity)}</b> &middot; source ${escapeHtml(p.source)}</div>
    ${p.area_desc ? `<div class="muted">${escapeHtml(p.area_desc.slice(0, 160))}</div>` : ""}
    <div class="muted">expires ${escapeHtml(expires)} &middot; v${p.version} &middot; ref ${escapeHtml(p.source_ref)}</div>
    ${p.is_drill ? `<button class="danger" data-cancel-drill="${p.id}">End drill</button>` : ""}
  </div>`;
}

export default function LiveMap(props: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  const latest = useRef(props);
  latest.current = props;
  const drillMarkers = useRef<maplibregl.Marker[]>([]);

  // Create the map once.
  useEffect(() => {
    if (!container.current) return;
    // Must be absolute: MapLibre starts the worker from a blob, where "/..." does not resolve.
    maplibregl.setWorkerUrl(new URL(WORKER_PATH, window.location.origin).href);
    const m = new maplibregl.Map({
      container: container.current,
      style: {
        version: 8,
        sources: {
          osm: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            maxzoom: 19,
            attribution: "&copy; OpenStreetMap contributors",
          },
        },
        layers: [{ id: "osm", type: "raster", source: "osm" }],
      },
      bounds: (props.initialBounds ?? undefined) as LngLatBoundsLike | undefined,
    });
    m.addControl(new maplibregl.NavigationControl(), "top-right");
    m.addControl(new maplibregl.ScaleControl({ unit: "metric" }));

    m.on("load", () => {
      m.addSource("hazards", { type: "geojson", data: EMPTY });
      m.addSource("facilities", { type: "geojson", data: EMPTY });
      m.addSource("draft", { type: "geojson", data: EMPTY });
      m.addSource("routes", { type: "geojson", data: EMPTY });
      m.addSource("vehicles", { type: "geojson", data: EMPTY });
      const color: maplibregl.ExpressionSpecification = [
        "match", ["get", "severity"],
        ...Object.entries(SEVERITY_COLORS).flat(),
        SEVERITY_COLORS.unknown,
      ] as unknown as maplibregl.ExpressionSpecification;
      m.addLayer({ id: "hazard-fill", type: "fill", source: "hazards",
        paint: { "fill-color": color, "fill-opacity": ["case", ["get", "is_drill"], 0.25, 0.35] } });
      m.addLayer({ id: "hazard-line", type: "line", source: "hazards", filter: ["!", ["get", "is_drill"]],
        paint: { "line-color": color, "line-width": 1.2 } });
      m.addLayer({ id: "drill-line", type: "line", source: "hazards", filter: ["get", "is_drill"],
        paint: { "line-color": "#6f2dbd", "line-width": 3, "line-dasharray": [2, 1.5] } });
      m.addLayer({ id: "facility-points", type: "circle", source: "facilities",
        paint: {
          "circle-radius": ["case", ["get", "is_supplier"], 6, 4],
          "circle-color": ["match", ["get", "kind"], "hospital", "#d6336c", "shelter", "#2b8a3e", "school", "#1971c2",
            "warehouse", "#5f3dc4", "godown", "#5f3dc4", "#495057"],
          "circle-stroke-color": "#fff", "circle-stroke-width": 1 } });
      m.addLayer({ id: "route-casing", type: "line", source: "routes", paint: { "line-color": "#fff", "line-width": 7 } });
      m.addLayer({ id: "route-line", type: "line", source: "routes", paint: { "line-color": "#0b7285", "line-width": 4 } });
      m.addLayer({ id: "vehicle-points", type: "circle", source: "vehicles",
        paint: { "circle-radius": 8, "circle-color": ["case", ["get", "is_offline"], "#868e96", "#0b7285"],
          "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
      m.addLayer({ id: "draft-fill", type: "fill", source: "draft", paint: { "fill-color": "#6f2dbd", "fill-opacity": 0.2 } });
      m.addLayer({ id: "draft-line", type: "line", source: "draft", paint: { "line-color": "#6f2dbd", "line-width": 2 } });
      m.addLayer({ id: "draft-pts", type: "circle", source: "draft", filter: ["==", ["geometry-type"], "Point"],
        paint: { "circle-radius": 4, "circle-color": "#6f2dbd" } });
      map.current = m;
      renderOverlays();
      renderHazards();
      loadFacilities();
    });

    m.on("click", (e: MapMouseEvent) => {
      if (latest.current.drawing) {
        latest.current.onMapClick([e.lngLat.lng, e.lngLat.lat]);
        return;
      }
      const hits = m.queryRenderedFeatures(e.point, { layers: ["vehicle-points", "facility-points", "hazard-fill"] });
      if (!hits.length) return;
      const hit = hits[0];
      const html = hit.layer.id === "vehicle-points"
        ? vehiclePopup(hit.properties as Record<string, unknown>)
        : hit.layer.id === "facility-points"
        ? facilityPopup(hit.properties as unknown as FacilityProperties)
        : hazardPopup(hit.properties as unknown as HazardProperties);
      const popup = new maplibregl.Popup({ maxWidth: "340px" }).setLngLat(e.lngLat).setHTML(html).addTo(m);
      popup.getElement().querySelector<HTMLButtonElement>("[data-cancel-drill]")?.addEventListener("click", (ev: MouseEvent) => {
        latest.current.onCancelDrill((ev.currentTarget as HTMLButtonElement).dataset.cancelDrill!);
        popup.remove();
      });
    });
    m.on("moveend", loadFacilities);

    return () => {
      m.remove();
      map.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function vehiclePopup(p: Record<string, unknown>): string {
    const seen = p.last_seen_at ? new Date(String(p.last_seen_at)).toLocaleTimeString() : "never";
    return `<div class="popup"><strong>${escapeHtml(String(p.label))}</strong>
      <div>${p.is_offline === true || p.is_offline === "true" ? '<span class="badge stale">OFFLINE</span>' : "online"} &middot; ${escapeHtml(String(p.status))}</div>
      <div class="muted">capacity ${p.capacity_kg} kg &middot; last GPS ${escapeHtml(seen)}</div></div>`;
  }

  function renderOverlays() {
    const m = map.current;
    if (!m) return;
    const { vehicles, routes } = latest.current;
    (m.getSource("vehicles") as GeoJSONSource | undefined)?.setData({
      type: "FeatureCollection",
      features: vehicles.filter((v) => v.position).map((v) => ({
        type: "Feature", geometry: v.position!,
        properties: { id: v.id, label: v.label, status: v.status, is_offline: v.is_offline, capacity_kg: v.capacity_kg, last_seen_at: v.last_seen_at },
      })),
    });
    (m.getSource("routes") as GeoJSONSource | undefined)?.setData({
      type: "FeatureCollection",
      features: routes.map((r) => ({ type: "Feature", geometry: r.geometry, properties: { id: r.id } })),
    });
  }

  useEffect(renderOverlays, [props.vehicles, props.routes]);

  function facilityPopup(p: FacilityProperties): string {
    return `<div class="popup"><strong>${escapeHtml(p.name)}</strong>
      <div>${escapeHtml(p.kind)}${p.is_supplier ? " &middot; registered supplier" : ""}</div>
      ${p.capacity_people ? `<div>capacity ${p.capacity_people}</div>` : ""}
      <div class="muted">${escapeHtml(p.source)} ${escapeHtml(p.source_ref)}</div></div>`;
  }

  async function loadFacilities() {
    const m = map.current;
    if (!m) return;
    const source = m.getSource("facilities") as GeoJSONSource | undefined;
    if (m.getZoom() < FACILITY_MIN_ZOOM) {
      source?.setData(EMPTY);
      latest.current.onFacilitiesStatus(`zoom in to level ${FACILITY_MIN_ZOOM} to load facilities`);
      return;
    }
    const b = m.getBounds();
    try {
      const fc = await api.facilities([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], FACILITY_KINDS);
      source?.setData(fc as unknown as GeoJSON.FeatureCollection);
      latest.current.onFacilitiesStatus(`${fc.features.length} facilities in view`);
    } catch (err) {
      latest.current.onFacilitiesStatus(`facilities unavailable: ${(err as Error).message}`);
    }
  }

  function renderHazards() {
    const m = map.current;
    const fc = latest.current.hazards;
    if (!m || !fc) return;
    (m.getSource("hazards") as GeoJSONSource).setData(fc as unknown as GeoJSON.FeatureCollection);
    // "DRILL" labels as HTML markers (the raster basemap has no font glyphs for symbol layers).
    drillMarkers.current.forEach((mk) => mk.remove());
    drillMarkers.current = fc.features
      .filter((f) => f.properties.is_drill && f.geometry.type === "MultiPolygon")
      .map((f) => {
        const ring = (f.geometry as GeoJSON.MultiPolygon).coordinates[0][0];
        const [sx, sy] = ring.reduce(([ax, ay], [x, y]) => [ax + x, ay + y], [0, 0]);
        const el = document.createElement("div");
        el.className = "drill-label";
        el.textContent = "DRILL";
        return new maplibregl.Marker({ element: el }).setLngLat([sx / ring.length, sy / ring.length]).addTo(m);
      });
  }

  useEffect(renderHazards, [props.hazards]);

  // Draft polygon while drawing a drill.
  useEffect(() => {
    const m = map.current;
    const src = m?.getSource("draft") as GeoJSONSource | undefined;
    if (!m || !src) return;
    const v = props.draftVertices;
    const features: GeoJSON.Feature[] = v.map((c) => ({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: c } }));
    if (v.length >= 3) {
      features.push({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [[...v, v[0]]] } });
    } else if (v.length === 2) {
      features.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: v } });
    }
    src.setData({ type: "FeatureCollection", features });
    m.getCanvas().style.cursor = props.drawing ? "crosshair" : "";
  }, [props.draftVertices, props.drawing]);

  return <div ref={container} className="map" />;
}
