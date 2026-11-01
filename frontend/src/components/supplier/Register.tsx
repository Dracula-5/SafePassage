"use client";

// First visit: register the organisation and choose where its stock is. Moved here unchanged from SupplierApp.

import { FormEvent, useEffect, useState } from "react";

import { Account, FacilityMatch, supplierApi } from "@/lib/api";

export default function Register({ account, onDone }: { account: Account; onDone: () => void }) {
  const [name, setName] = useState(account.name);
  const [phone, setPhone] = useState("");
  const [whatsapp, setWhatsapp] = useState("");
  const [language, setLanguage] = useState("te");
  const [query, setQuery] = useState("");
  // Kept with the text it answers, so an older reply is never shown for newer typing. null matches: the search failed.
  const [found, setFound] = useState<{ text: string; matches: FacilityMatch[] | null } | null>(null);
  const [facility, setFacility] = useState<FacilityMatch | null>(null);
  const [position, setPosition] = useState<[number, number] | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const text = query.trim();
  const searched = found?.text === text ? found : null;
  const matches = searched?.matches ?? [];

  useEffect(() => {
    if (text.length < 2) return;
    let current = true;
    const timer = setTimeout(() => {
      supplierApi.search(text, position).then(
        (result) => { if (current) setFound({ text, matches: result }); },
        () => { if (current) setFound({ text, matches: null }); },
      );
    }, 300);
    return () => { current = false; clearTimeout(timer); };
  }, [text, position]);

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
        {facility ? <div className="alert ok">Selected: {facility.name}</div> : text.length >= 2 && (
          <ul className="picker">
            {matches.map((m) => (
              <li key={m.id}><button type="button" onClick={() => { setFacility(m); setPosition(null); setNote(null); }}>
                {m.name} <small className="muted">{m.kind}{m.distance_m != null ? `, ${(m.distance_m / 1000).toFixed(1)} km away` : ""}</small>
              </button></li>
            ))}
            {!searched && <li className="empty" role="status">Searching…</li>}
            {searched?.matches === null && <li className="empty" role="alert">Search is unavailable right now. Try again in a moment, or use your current location.</li>}
            {searched?.matches?.length === 0 && <li className="empty">No matching warehouses found.</li>}
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
