"use client";

// Supplier's screen: register an organisation once, then keep its real stock up to date. Planners only
// allocate what is reported here, so "reserved" moves by itself when a dispatch takes stock.

import { FormEvent, useCallback, useEffect, useState } from "react";

import { Account, ApiError, FacilityMatch, supplierApi, SupplierOrder, SupplierProfile } from "@/lib/api";
import { ago, itemName } from "@/lib/format";
import { useAccount } from "@/lib/session";

import AppShell, { Loading } from "./AppShell";
import LiveStatus, { REFRESH_MS } from "./LiveStatus";

export default function SupplierApp() {
  const account = useAccount("supplier");
  const [profile, setProfile] = useState<SupplierProfile | null>(null);
  const [orders, setOrders] = useState<SupplierOrder[]>([]);
  const [unregistered, setUnregistered] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    try {
      setProfile(await supplierApi.me());
      setOrders(await supplierApi.orders());
      setUnregistered(false);
      setFailed(false);
      setUpdatedAt(Date.now());
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) setUnregistered(true);
      else setFailed(true);
    }
  }, []);

  useEffect(() => {
    if (!account) return;
    const first = setTimeout(load, 0);
    const timer = setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [account, load]);

  if (!account) return <Loading />;
  return (
    <AppShell account={account} title="Supplier" status={profile && <LiveStatus updatedAt={updatedAt} failed={failed} />}>
      <main className="page">
        {unregistered ? <Register account={account} onDone={load} />
          : profile ? <Stock profile={profile} orders={orders} onSaved={load} />
          : <Loading />}
      </main>
    </AppShell>
  );
}

function Register({ account, onDone }: { account: Account; onDone: () => void }) {
  const [name, setName] = useState(account.name);
  const [phone, setPhone] = useState("");
  const [whatsapp, setWhatsapp] = useState("");
  const [language, setLanguage] = useState("te");
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<FacilityMatch[]>([]);
  const [facility, setFacility] = useState<FacilityMatch | null>(null);
  const [position, setPosition] = useState<[number, number] | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (query.trim().length < 2) return;
    const timer = setTimeout(() => supplierApi.search(query.trim(), position).then(setMatches, () => undefined), 300);
    return () => clearTimeout(timer);
  }, [query, position]);

  function useMyLocation() {
    navigator.geolocation.getCurrentPosition(
      (p) => {
        setPosition([p.coords.longitude, p.coords.latitude]);
        setFacility(null);
        setNote(`Using your location, ±${Math.round(p.coords.accuracy)} m.`);
      },
      (e) => setNote(`Location unavailable: ${e.message}`),
      { enableHighAccuracy: true, timeout: 15000 },
    );
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!facility && !position) return setError("Choose your site, or use your current location.");
    const place = facility ? { facility_id: facility.id } : { lon: position![0], lat: position![1], site_name: name.trim() };
    try {
      await supplierApi.register({ name: name.trim(), contact_phone: phone.trim() || undefined, contact_whatsapp: whatsapp.trim() || undefined,
        preferred_language: language, ...place });
      onDone();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <section className="card narrow">
      <h1>Register your organisation</h1>
      <p className="muted">Tell us where your stock is. You only do this once.</p>
      <form className="form" onSubmit={submit}>
        <label>Organisation name<input required minLength={2} maxLength={120} value={name} onChange={(e) => setName(e.target.value)} /></label>
        <div className="row">
          <label>Phone<input inputMode="tel" placeholder="+9198xxxxxxxx" value={phone} onChange={(e) => setPhone(e.target.value)} /></label>
          <label>WhatsApp<input inputMode="tel" placeholder="+9198xxxxxxxx" value={whatsapp} onChange={(e) => setWhatsapp(e.target.value)} /></label>
        </div>
        <label>Language
          <select value={language} onChange={(e) => setLanguage(e.target.value)}>
            <option value="te">తెలుగు (Telugu)</option>
            <option value="hi">हिन्दी (Hindi)</option>
            <option value="en">English</option>
          </select>
        </label>
        <label>Find your warehouse or site
          <input placeholder="Type at least 2 letters of its name" value={query} onChange={(e) => { setQuery(e.target.value); setFacility(null); }} />
        </label>
        {facility ? <div className="alert ok">Selected: {facility.name}</div> : query.trim().length >= 2 && (
          <ul className="picker">
            {matches.map((m) => (
              <li key={m.id}><button type="button" onClick={() => { setFacility(m); setPosition(null); setNote(null); }}>
                {m.name} <small className="muted">{m.kind}{m.distance_m != null ? `, ${(m.distance_m / 1000).toFixed(1)} km away` : ""}</small>
              </button></li>
            ))}
            {matches.length === 0 && <li className="empty">No match yet.</li>}
          </ul>
        )}
        <button type="button" onClick={useMyLocation}>Not listed? Use my current location</button>
        {note && <p className="small muted">{note}</p>}
        {error && <div className="alert danger" role="alert">{error}</div>}
        <button className="primary">Register</button>
      </form>
    </section>
  );
}

const ORDER_STATUS: Record<string, { text: string; tone: string }> = {
  dispatched: { text: "Prepare for pickup", tone: "orange" },
  in_transit: { text: "On the truck", tone: "blue" },
  delivered: { text: "Delivered", tone: "green" },
};

function Stock({ profile, orders, onSaved }: { profile: SupplierProfile; orders: SupplierOrder[]; onSaved: () => void }) {
  const units = Object.fromEntries(profile.items.map((i) => [i.code, i.unit]));
  const [item, setItem] = useState(profile.items[0]?.code ?? "");
  const [quantity, setQuantity] = useState("");
  const [weight, setWeight] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await supplierApi.report([{ item_code: item, quantity: Number(quantity), unit_weight_kg: Number(weight) }]);
      setMsg({ ok: true, text: `${itemName(item)} updated. Planners can see it now.` });
      setQuantity("");
      onSaved();
    } catch (err) {
      setMsg({ ok: false, text: (err as Error).message });
    }
  }

  return (
    <>
      <section className="page-head">
        <div>
          <h1>{profile.name}</h1>
          <p className="muted">{profile.site}</p>
        </div>
        <span className={profile.verified ? "status green" : "status orange"}>{profile.verified ? "Verified" : "Awaiting verification"}</span>
      </section>
      {!profile.verified && <div className="alert warn">A duty officer checks any plan that uses your stock until your organisation is verified.</div>}

      <section className="card">
        <h2>Orders</h2>
        {orders.length === 0 ? <p className="empty">No orders yet. When a duty officer approves a plan that uses your stock, it appears here.</p> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Goods</th><th>Going to</th><th>Status</th><th>Ordered</th></tr></thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.id}>
                    <td><b>{o.lines.map((l) => `${Math.round(l.quantity).toLocaleString()} ${itemName(l.item_code)}`).join(", ")}</b></td>
                    <td>{o.destination}</td>
                    <td>
                      <span className={`status ${ORDER_STATUS[o.status]?.tone ?? "grey"}`}>{ORDER_STATUS[o.status]?.text ?? o.status.replace("_", " ")}</span>
                      {o.is_drill && <span className="badge drill">DRILL</span>}
                    </td>
                    <td className="muted small">{ago(o.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="page-grid">
        <section className="card">
          <h2>Your stock</h2>
          {profile.stock.length === 0 ? <p className="empty">Nothing reported yet. Add what you hold, and planners can use it straight away.</p> : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Item</th><th className="num">Held</th><th className="num">Reserved</th><th className="num">Available</th><th>Reported</th></tr></thead>
                <tbody>
                  {profile.stock.map((s) => (
                    <tr key={s.item_code}>
                      <td><b>{itemName(s.item_code)}</b> <small className="muted">{units[s.item_code]}</small></td>
                      <td className="num">{s.quantity.toLocaleString()}</td>
                      <td className="num">{s.reserved.toLocaleString()}</td>
                      <td className="num"><b>{s.available.toLocaleString()}</b></td>
                      <td className="muted small">{ago(s.reported_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="card">
          <h2>Report stock</h2>
          <p className="muted small">Report what you actually hold. This replaces the last figure for that item.</p>
          <form className="form" onSubmit={submit}>
            <label>Item
              <select value={item} onChange={(e) => setItem(e.target.value)}>
                {profile.items.map((i) => <option key={i.code} value={i.code}>{itemName(i.code)} ({i.unit})</option>)}
              </select>
            </label>
            <div className="row">
              <label>Quantity<input required type="number" min={0} step="any" inputMode="decimal" value={quantity} onChange={(e) => setQuantity(e.target.value)} /></label>
              <label>Weight per unit (kg)<input required type="number" min={0.001} step="any" inputMode="decimal" value={weight} onChange={(e) => setWeight(e.target.value)} /></label>
            </div>
            {msg && <div className={msg.ok ? "alert ok" : "alert danger"} role="status">{msg.text}</div>}
            <button className="primary">Save stock</button>
          </form>
        </section>
      </div>
    </>
  );
}
