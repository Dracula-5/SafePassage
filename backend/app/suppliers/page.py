"""Supplier check-in page. Static HTML + vanilla JS so it works on any phone browser."""

PORTAL_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>ReliefRoute supplier check-in</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:560px;margin:0 auto;padding:16px;color:#1d2329}
 h1{font-size:20px} h2{font-size:16px;margin-top:24px} label{display:block;margin:8px 0 2px;font-size:14px}
 input,select,button{font-size:16px;padding:8px;width:100%;box-sizing:border-box}
 button{margin-top:10px;background:#1971c2;color:#fff;border:0;border-radius:6px}
 .row{display:grid;grid-template-columns:2fr 1fr 1fr auto;gap:6px;align-items:end}
 .row button{width:auto;background:#adb5bd} .muted{color:#6c757d;font-size:13px}
 .ok{background:#ebfbee;padding:8px;border-radius:6px} .err{background:#fff0f0;padding:8px;border-radius:6px}
 li{font-size:14px}
</style></head><body>
<h1>ReliefRoute: supplier check-in</h1>
<p class="muted">Report the stock you actually hold. Planners only allocate what suppliers report here.</p>

<section id="register">
 <h2>1. Register (once)</h2>
 <label>Supplier / organisation name<input id="r-name" maxlength="120"></label>
 <label>Phone (E.164, e.g. +9198xxxxxxxx)<input id="r-phone" inputmode="tel"></label>
 <label>WhatsApp number (optional)<input id="r-wa" inputmode="tel"></label>
 <label>Language<select id="r-lang"><option value="te">Telugu</option><option value="hi">Hindi</option><option value="en">English</option></select></label>
 <label>Find your warehouse / facility<input id="r-q" placeholder="type 2+ letters of its name"></label>
 <select id="r-fac" size="5"></select>
 <p class="muted">Not listed? <button type="button" id="r-gps" style="width:auto;background:#495057">Use my current location instead</button> <span id="r-gps-msg"></span></p>
 <button id="r-submit">Register</button>
 <div id="r-msg"></div>
</section>

<section>
 <h2>2. Report stock</h2>
 <p class="muted" id="who"></p>
 <div id="lines"></div>
 <button type="button" id="add" style="background:#495057">+ Add item</button>
 <button id="s-submit">Submit stock</button>
 <div id="s-msg"></div>
 <ul id="levels"></ul>
</section>

<template id="line-tpl"><div class="row">
 <label>Item<select class="item">{{ITEM_OPTIONS}}</select></label>
 <label>Quantity<input class="qty" type="number" min="0" step="any" inputmode="decimal"></label>
 <label>kg per unit<input class="w" type="number" min="0" step="any" inputmode="decimal"></label>
 <button type="button" class="rm">x</button></div></template>

<script>
const $ = (id) => document.getElementById(id);
let gps = null;
const token = () => localStorage.getItem("rr_supplier_token");
function show(el, ok, text) { el.className = ok ? "ok" : "err"; el.textContent = text; }
function refreshWho() { $("who").textContent = token() ? "Registered on this device (" + (localStorage.getItem("rr_supplier_name") || "") + ")." : "Register first (step 1)."; }
async function api(path, opts) {
  const res = await fetch(path, opts); const text = await res.text();
  let body; try { body = JSON.parse(text); } catch { body = text; }
  if (!res.ok) throw new Error(typeof body === "object" ? JSON.stringify(body.detail ?? body) : body);
  return body;
}
let timer;
$("r-q").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(async () => {
  const q = $("r-q").value.trim(); if (q.length < 2) return;
  const params = new URLSearchParams({ q }); if (gps) { params.set("lon", gps[0]); params.set("lat", gps[1]); }
  const rows = await api("/portal/api/facilities?" + params);
  $("r-fac").innerHTML = rows.map(r => `<option value="${r.id}">${r.name.replace(/</g,"&lt;")} (${r.kind}${r.distance_m != null ? ", " + (r.distance_m/1000).toFixed(1) + " km" : ""})</option>`).join("");
}, 300); });
$("r-gps").addEventListener("click", () => navigator.geolocation.getCurrentPosition(
  (p) => { gps = [p.coords.longitude, p.coords.latitude]; $("r-gps-msg").textContent = `using ${gps[1].toFixed(5)}, ${gps[0].toFixed(5)} (±${Math.round(p.coords.accuracy)} m)`; $("r-fac").selectedIndex = -1; },
  (e) => { $("r-gps-msg").textContent = "location unavailable: " + e.message; }, { enableHighAccuracy: true, timeout: 15000 }));
$("r-submit").addEventListener("click", async () => {
  const body = { name: $("r-name").value.trim(), preferred_language: $("r-lang").value };
  if ($("r-phone").value.trim()) body.contact_phone = $("r-phone").value.trim();
  if ($("r-wa").value.trim()) body.contact_whatsapp = $("r-wa").value.trim();
  if ($("r-fac").value) body.facility_id = $("r-fac").value;
  else if (gps) { body.lon = gps[0]; body.lat = gps[1]; body.site_name = body.name; }
  try {
    const r = await api("/portal/api/register", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    localStorage.setItem("rr_supplier_token", r.supplier_token); localStorage.setItem("rr_supplier_name", body.name);
    show($("r-msg"), true, "Registered. Your token is saved on this device. Status: awaiting verification."); refreshWho();
  } catch (e) { show($("r-msg"), false, e.message); }
});
function addLine() { const n = $("line-tpl").content.cloneNode(true); n.querySelector(".rm").onclick = (e) => e.target.closest(".row").remove(); $("lines").appendChild(n); }
$("add").addEventListener("click", addLine); addLine();
$("s-submit").addEventListener("click", async () => {
  if (!token()) return show($("s-msg"), false, "Register first.");
  const lines = [...document.querySelectorAll("#lines .row")].map(r => ({ item_code: r.querySelector(".item").value,
    quantity: Number(r.querySelector(".qty").value), unit_weight_kg: Number(r.querySelector(".w").value) }))
    .filter(l => !Number.isNaN(l.quantity) && l.unit_weight_kg > 0);
  if (!lines.length) return show($("s-msg"), false, "Enter quantity and kg per unit.");
  try {
    const levels = await api("/portal/api/stock", { method: "POST", headers: { "Content-Type": "application/json", "X-Supplier-Token": token() }, body: JSON.stringify({ lines }) });
    show($("s-msg"), true, "Stock saved.");
    $("levels").innerHTML = levels.map(l => `<li>${l.item_code}: ${l.quantity} held, ${l.reserved} reserved, <b>${l.available}</b> available</li>`).join("");
  } catch (e) { show($("s-msg"), false, e.message); }
});
refreshWho();
</script></body></html>
"""
