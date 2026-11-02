"use client";

// The front door. Signed-in people go straight to their screen; everyone else sees the live picture.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { authApi } from "@/lib/api";
import { HOME } from "@/lib/session";

import { Loading, Logo } from "./AppShell";
import { usePublicStatus } from "./AuthForms";

const ROLES = [
  { role: "operator", title: "Duty officers", text: "See every hazard, truck and plan as it changes, and approve each dispatch." },
  { role: "driver", title: "Drivers", text: "Get a safe route in your language, read aloud, even through dead zones." },
  { role: "supplier", title: "Suppliers", text: "Report the stock you hold, so planners only promise what really exists." },
];

export default function Landing() {
  const router = useRouter();
  const status = usePublicStatus();
  const [visitor, setVisitor] = useState(false);

  useEffect(() => {
    authApi.me().then((me) => router.replace(HOME[me.role]), () => setVisitor(true));
  }, [router]);

  if (!visitor) return <Loading />;
  return (
    <div className="landing">
      <header className="landing-top">
        <span className="brand"><Logo size={32} /><span>ReliefRoute</span></span>
        <span className="spacer" />
        <Link className="button ghost-light" href="/signin">Sign in</Link>
        <Link className="button primary" href="/signup">Create account</Link>
      </header>
      <section className="hero">
        <span className="eyebrow"><span className="live-dot" /> Live across Andhra Pradesh</span>
        <h1>Relief goods, on roads that are actually safe.</h1>
        <p>ReliefRoute turns live disaster alerts into delivery plans that avoid danger zones. People stay in charge: nothing is dispatched until a duty officer approves it.</p>
        <dl className="hero-stats" aria-live="polite">
          <div><dt>Active alerts</dt><dd>{status?.hazards_active ?? "–"}</dd></div>
          <div><dt>Severe or worse</dt><dd>{status?.hazards_severe ?? "–"}</dd></div>
          <div><dt>Live data sources</dt><dd>{status ? `${status.sources_fresh}/${status.sources_total}` : "–"}</dd></div>
        </dl>
      </section>
      <section className="role-cards">
        {ROLES.map((r) => (
          <Link key={r.role} href={`/signup?role=${r.role}`} className="role-card">
            <h2>{r.title}</h2>
            <p>{r.text}</p>
            <span>Get started →</span>
          </Link>
        ))}
      </section>
    </div>
  );
}
