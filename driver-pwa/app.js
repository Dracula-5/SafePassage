// ReliefRoute driver app: real phone GPS -> POST /api/telemetry (batched, offline-tolerant queue),
// assignment from GET /api/vehicles/{id}/assignment, voice instructions via the Web Speech API.
"use strict";
const $ = (id) => document.getElementById(id);
const store = {
  get vehicle() { return JSON.parse(localStorage.getItem("rr_vehicle") || "null"); },
  set vehicle(v) { localStorage.setItem("rr_vehicle", JSON.stringify(v)); },
  get queue() { return JSON.parse(localStorage.getItem("rr_queue") || "[]"); },
  set queue(q) { localStorage.setItem("rr_queue", JSON.stringify(q.slice(-2000))); }, // cap: ~1 day at 1 fix / 45 s
};
const SPEECH_LANG = { te: "te-IN", hi: "hi-IN", en: "en-IN" };
const MIN_FIX_INTERVAL_MS = 10_000;
let lastFixAt = 0;
let instructions = null;

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const text = await res.text();
  let body; try { body = JSON.parse(text); } catch { body = text; }
  if (!res.ok) throw Object.assign(new Error(body.detail || body.title || text), { status: res.status });
  return body;
}

function show() {
  const v = store.vehicle;
  $("register").hidden = !!v;
  $("driving").hidden = !v;
  if (v) $("v-label").textContent = v.vehicle.label;
  $("net").innerHTML = navigator.onLine ? '<span class="ok">online</span>' : '<span class="bad">offline (queueing)</span>';
  $("queued").textContent = store.queue.length;
}

$("do-register").onclick = async () => {
  const body = { label: $("label").value.trim(), driver_name: $("name").value.trim() || undefined,
    driver_phone: $("phone").value.trim() || undefined, driver_language: $("lang").value, capacity_kg: Number($("cap").value) };
  try {
    store.vehicle = await api("/api/vehicles", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    show(); startGps();
  } catch (e) { $("reg-msg").className = "err"; $("reg-msg").textContent = e.message; }
};

function startGps() {
  if (!("geolocation" in navigator)) { $("gps").textContent = "not supported"; return; }
  navigator.geolocation.watchPosition((p) => {
    const now = Date.now();
    $("gps").textContent = `${p.coords.latitude.toFixed(5)}, ${p.coords.longitude.toFixed(5)} (±${Math.round(p.coords.accuracy)} m)`;
    if (now - lastFixAt < MIN_FIX_INTERVAL_MS) return;
    lastFixAt = now;
    store.queue = [...store.queue, { lon: p.coords.longitude, lat: p.coords.latitude, recorded_at: new Date(p.timestamp).toISOString(),
      speed_mps: p.coords.speed ?? null, heading_deg: p.coords.heading != null && p.coords.heading < 360 ? p.coords.heading : null,
      accuracy_m: p.coords.accuracy }];
    show();
  }, (e) => { $("gps").innerHTML = `<span class="bad">${e.message}</span>`; }, { enableHighAccuracy: true, maximumAge: 5000, timeout: 30000 });
}

async function flush() {
  const v = store.vehicle;
  const batch = store.queue.slice(0, 500);
  if (!v || !batch.length || !navigator.onLine) return;
  try {
    const r = await api("/api/telemetry", { method: "POST", headers: { "Content-Type": "application/json", "X-Device-Token": v.device_token },
      body: JSON.stringify({ vehicle_id: v.vehicle.id, fixes: batch }) });
    store.queue = store.queue.slice(batch.length); // duplicates are safe: the server ignores resends
    $("sent").textContent = `${new Date().toLocaleTimeString()} (${r.accepted} new, ${r.duplicates} dup)`;
  } catch (e) {
    if (e.status === 401) { $("sent").innerHTML = '<span class="bad">device not recognised: sign out and register again</span>'; }
    else if (e.status === 422) { store.queue = store.queue.slice(batch.length); } // stale fixes rejected by server: drop them
    else { $("sent").innerHTML = `<span class="bad">retrying: ${e.message}</span>`; }
  }
  show();
}

function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]); }

async function loadAssignment() {
  const v = store.vehicle;
  if (!v || !navigator.onLine) return;
  try {
    const a = await api(`/api/vehicles/${v.vehicle.id}/assignment`);
    instructions = a.instructions;
    const stops = a.route.stops.map((s) => `<li><b>${s.kind === "pickup" ? "Pick up" : "Deliver"}</b> ${esc(s.name)}, by ${new Date(s.eta).toLocaleTimeString()}</li>`).join("");
    $("assignment").innerHTML = (a.is_drill ? '<p><span class="drill">DRILL - NOT A REAL DISPATCH</span></p>' : "")
      + `<p>${(a.route.distance_m / 1000).toFixed(0)} km · about ${(a.route.duration_s / 3600).toFixed(1)} h · avoids active hazard zones</p>`
      + `<ol>${stops}</ol><div class="instr">${esc(a.instructions.text)}</div>`;
    $("speak").hidden = !("speechSynthesis" in window);
  } catch (e) {
    if (e.status === 404) { instructions = null; $("assignment").innerHTML = "<p>No assignment yet. Keep this screen open; it refreshes automatically.</p>"; $("speak").hidden = true; }
  }
}

$("speak").onclick = () => {
  if (!instructions) return;
  const u = new SpeechSynthesisUtterance(instructions.text);
  u.lang = SPEECH_LANG[instructions.language] || instructions.language;
  const voice = speechSynthesis.getVoices().find((vo) => vo.lang === u.lang);
  if (voice) u.voice = voice;
  speechSynthesis.cancel(); speechSynthesis.speak(u);
};
$("forget").onclick = () => { if (confirm("Sign out? Queued GPS fixes will be discarded.")) { localStorage.clear(); location.reload(); } };
window.addEventListener("online", () => { show(); flush(); loadAssignment(); });
window.addEventListener("offline", show);
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/driver/sw.js", { scope: "/driver/" });
show();
if (store.vehicle) startGps();
setInterval(flush, 15_000);
setInterval(loadAssignment, 20_000);
loadAssignment();
