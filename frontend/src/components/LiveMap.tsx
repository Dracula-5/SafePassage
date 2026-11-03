"use client";

// MapLibre map: live hazard polygons, facilities in view, and a polygon drawing mode for drills.
// All features come from the backend; the map only renders them.

import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource, LngLatBoundsLike, Map as MlMap, MapMouseEvent } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";

import { api, FacilityProperties, FeatureCollection, HazardProperties, LiveRoute, PlanRoute, Vehicle } from "@/lib/api";
import { plate } from "@/lib/format";

import { EVACUATION_ROUTE, SEVERITY_COLORS, SIMULATED_TRUCK, SUPPLIER_STOCK, SUPPLY_ROUTE, WAREHOUSE } from "./ops/constants";

const FACILITY_KINDS = ["hospital", "shelter", "warehouse", "godown", "school", "depot"];
const STORAGE_KINDS = ["warehouse", "godown", "depot"];
// At state zoom some 700 warehouses covered the hazards; from a few districts across they help, so they wait for it.
const WAREHOUSE_MIN_ZOOM = 7;
const FACILITY_MIN_ZOOM = 9; // hospitals, schools and shelters: below this, tens of thousands of points would be requested
const IS_STORAGE: maplibregl.ExpressionSpecification = ["in", ["get", "kind"], ["literal", STORAGE_KINDS]];

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
  onFindDrivers: (hazardId: string) => void;
  onAskTruck: (vehicleId: string) => void;
  onEvacuate: (hazardId: string) => void;
  vehicles: Vehicle[];
  routes: PlanRoute[];
  liveRoutes: LiveRoute[];
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

/** A small warehouse, the shape of WarehouseGlyph in ops/parts.tsx, drawn at twice its size for sharp screens. */
function warehouseIcon(fill: string): ImageData {
  const size = 32;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const g = canvas.getContext("2d")!;
  g.fillStyle = fill;
  g.strokeStyle = "#fff";
  g.lineWidth = 3;
  g.beginPath();
  g.roundRect(2, 2, 28, 28, 7);
  g.fill();
  g.stroke();
  g.fillStyle = "#fff";
  g.beginPath();
  g.moveTo(8, 15);
  g.lineTo(16, 8);
  g.lineTo(24, 15);
  g.closePath();
  g.fill();
  g.fillRect(10, 15, 12, 9);
  g.fillStyle = fill;
  g.fillRect(14, 18, 4, 6);
  return g.getImageData(0, 0, size, size);
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
    <button data-find-drivers="${p.id}">Find nearest drivers</button>
    <button data-evacuate="${p.id}">Request evacuation</button>
    ${p.is_drill ? `<button class="danger" data-cancel-drill="${p.id}">End drill</button>` : ""}
  </div>`;
}

export default function LiveMap(props: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  const latest = useRef(props);
  latest.current = props;
  const drillMarkers = useRef<maplibregl.Marker[]>([]);
  const truckNumbers = useRef(new Map<string, maplibregl.Marker>());

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
            tiles: [`${window.location.origin}/tiles/{z}/{x}/{y}.png`],
            tileSize: 256,
            maxzoom: 19,
            attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
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
      m.addLayer({ id: "facility-points", type: "circle", source: "facilities", filter: ["!", IS_STORAGE],
        paint: {
          "circle-radius": ["case", ["get", "is_supplier"], 6, 4],
          "circle-color": ["match", ["get", "kind"], "hospital", "#d6336c", "shelter", "#2b8a3e", "school", "#1971c2", "#495057"],
          "circle-stroke-color": "#fff", "circle-stroke-width": 1 } });
      m.addImage("warehouse", warehouseIcon(WAREHOUSE), { pixelRatio: 2 });
      m.addImage("supplier", warehouseIcon(SUPPLIER_STOCK), { pixelRatio: 2 });
      // An icon that would overlap another is left out, registered suppliers first: a few per town at district zoom,
      // every one up close. Icons need no font, so this symbol layer works on the raster basemap.
      m.addLayer({ id: "warehouse-icons", type: "symbol", source: "facilities", filter: IS_STORAGE, minzoom: WAREHOUSE_MIN_ZOOM,
        layout: {
          "icon-image": ["case", ["get", "is_supplier"], "supplier", "warehouse"],
          "icon-size": ["interpolate", ["linear"], ["zoom"], WAREHOUSE_MIN_ZOOM, 0.75, 13, 1.1],
          "icon-allow-overlap": false,
          // Wide spacing when zoomed out, so an area shows a handful; it closes up as the map does.
          "icon-padding": ["interpolate", ["linear"], ["zoom"], WAREHOUSE_MIN_ZOOM, 18, 9, 9, 11, 3],
          "symbol-sort-key": ["case", ["get", "is_supplier"], 0, 1],
        } });
      m.addLayer({ id: "route-casing", type: "line", source: "routes", paint: { "line-color": "#fff", "line-width": 7 } });
      m.addLayer({ id: "route-line", type: "line", source: "routes",
        paint: { "line-color": ["match", ["get", "kind"], "evacuation", EVACUATION_ROUTE, SUPPLY_ROUTE], "line-width": 4 } });
      m.addLayer({ id: "vehicle-points", type: "circle", source: "vehicles",
        paint: { "circle-radius": 11, "circle-color": ["case", ["get", "is_offline"], "#adb5bd", "#fff"],
          "circle-stroke-color": ["case", ["get", "simulated"], SIMULATED_TRUCK, "#0b7285"], "circle-stroke-width": 2 } });
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
      const hits = m.queryRenderedFeatures(e.point, { layers: ["vehicle-points", "warehouse-icons", "facility-points", "hazard-fill"] });
      if (!hits.length) return;
      const hit = hits[0];
      const html = hit.layer.id === "vehicle-points"
        ? vehiclePopup(hit.properties as Record<string, unknown>)
        : hit.layer.id === "facility-points" || hit.layer.id === "warehouse-icons"
        ? facilityPopup(hit.properties as unknown as FacilityProperties)
        : hazardPopup(hit.properties as unknown as HazardProperties);
      const popup = new maplibregl.Popup({ maxWidth: "340px" }).setLngLat(e.lngLat).setHTML(html).addTo(m);
      popup.getElement().querySelector<HTMLButtonElement>("[data-cancel-drill]")?.addEventListener("click", (ev: MouseEvent) => {
        latest.current.onCancelDrill((ev.currentTarget as HTMLButtonElement).dataset.cancelDrill!);
        popup.remove();
      });
      popup.getElement().querySelector<HTMLButtonElement>("[data-find-drivers]")?.addEventListener("click", (ev: MouseEvent) => {
        latest.current.onFindDrivers((ev.currentTarget as HTMLButtonElement).dataset.findDrivers!);
        popup.remove();
      });
      popup.getElement().querySelector<HTMLButtonElement>("[data-evacuate]")?.addEventListener("click", (ev: MouseEvent) => {
        latest.current.onEvacuate((ev.currentTarget as HTMLButtonElement).dataset.evacuate!);
        popup.remove();
      });
      popup.getElement().querySelector<HTMLButtonElement>("[data-ask-truck]")?.addEventListener("click", (ev: MouseEvent) => {
        latest.current.onAskTruck((ev.currentTarget as HTMLButtonElement).dataset.askTruck!);
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
    const offline = p.is_offline === true || p.is_offline === "true";
    const onRoute = p.on_route === true || p.on_route === "true";
    return `<div class="popup"><strong>Truck ${escapeHtml(String(p.fleet_number))} &middot; ${escapeHtml(plate(String(p.label)))}</strong>
      ${p.simulated === true || p.simulated === "true" ? '<span class="badge sim">SIMULATED</span>' : ""}
      ${p.driver_name ? `<div>${escapeHtml(String(p.driver_name))}</div>` : ""}
      <div>${offline ? '<span class="badge stale">OFFLINE</span>' : "online"} &middot; ${onRoute ? "on a route" : "waiting"}</div>
      <div class="muted">capacity ${p.capacity_kg} kg &middot; last GPS ${escapeHtml(seen)}</div>
      <a href="/driver-view?vehicle=${escapeHtml(String(p.id))}" target="_blank" rel="noreferrer">Driver screen</a>
      ${offline || onRoute ? "" : `<button data-ask-truck="${escapeHtml(String(p.id))}">Send request</button>`}</div>`;
  }

  function renderOverlays() {
    const m = map.current;
    if (!m) return;
    const { routes, liveRoutes } = latest.current;
    const vehicles = latest.current.vehicles.filter((v) => v.position && v.status !== "maintenance");
    (m.getSource("vehicles") as GeoJSONSource | undefined)?.setData({
      type: "FeatureCollection",
      features: vehicles.map((v) => ({
        type: "Feature", geometry: v.position!,
        properties: { id: v.id, label: v.label, status: v.status, is_offline: v.is_offline, capacity_kg: v.capacity_kg, last_seen_at: v.last_seen_at,
          simulated: Boolean(v.simulated), on_route: Boolean(v.on_route), driver_name: v.driver_name, fleet_number: v.fleet_number },
      })),
    });
    // The number sits on the round button as HTML: the raster basemap has no font glyphs for symbol layers.
    // It lets clicks through, so the button underneath still opens the popup and a drill can be drawn across it.
    const numbers = truckNumbers.current;
    for (const [id, marker] of numbers) {
      if (!vehicles.some((v) => v.id === id)) {
        marker.remove();
        numbers.delete(id);
      }
    }
    for (const v of vehicles) {
      const at = v.position!.coordinates as [number, number];
      const known = numbers.get(v.id);
      if (known) {
        known.setLngLat(at);
        continue;
      }
      const el = document.createElement("div");
      el.className = "truck-number";
      el.textContent = String(v.fleet_number);
      numbers.set(v.id, new maplibregl.Marker({ element: el }).setLngLat(at).addTo(m));
    }
    (m.getSource("routes") as GeoJSONSource | undefined)?.setData({
      type: "FeatureCollection",
      // Every route on the road now, plus the selected plan's finished ones. Supply first, so green draws on top.
      features: [...new Map([...routes, ...liveRoutes].map((r) => [r.id, r])).values()]
        .sort((a, b) => Number(a.kind === "evacuation") - Number(b.kind === "evacuation"))
        .map((r) => ({ type: "Feature", geometry: r.geometry, properties: { id: r.id, kind: r.kind } })),
    });
  }

  useEffect(renderOverlays, [props.vehicles, props.routes, props.liveRoutes]);

  // At state zoom a route is a few pixels; show it when a plan with routes is selected. Keyed on the
  // route ids so a refresh of the same plan does not move the map under the operator.
  const routeKey = props.routes.map((r) => r.id).join(",");
  useEffect(() => {
    const coordinates = latest.current.routes.flatMap((r) => r.geometry.coordinates as [number, number][]);
    if (!coordinates.length) return;
    const bounds = coordinates.reduce((b, c) => b.extend(c), new maplibregl.LngLatBounds(coordinates[0], coordinates[0]));
    map.current?.fitBounds(bounds, { padding: 70, maxZoom: 13, duration: 800 });
  }, [routeKey]);

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
    if (m.getZoom() < WAREHOUSE_MIN_ZOOM) {
      source?.setData(EMPTY);
      latest.current.onFacilitiesStatus("Zoom in to see warehouses, and closer for hospitals, schools and shelters");
      return;
    }
    const close = m.getZoom() >= FACILITY_MIN_ZOOM;
    const b = m.getBounds();
    try {
      const fc = await api.facilities(
        [Math.max(b.getWest(), -180), Math.max(b.getSouth(), -90), Math.min(b.getEast(), 180), Math.min(b.getNorth(), 90)],
        close ? FACILITY_KINDS : STORAGE_KINDS,
      );
      source?.setData(fc as unknown as GeoJSON.FeatureCollection);
      latest.current.onFacilitiesStatus(close
        ? `${fc.features.length} facilities in view`
        : `${fc.features.length} warehouses and godowns in view. Zoom in to see each one, and hospitals, schools and shelters`);
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
