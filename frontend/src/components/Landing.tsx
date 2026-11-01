"use client";

// The front door. Signed-in people go straight to their screen; everyone else sees the live picture and one next step.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { authApi } from "@/lib/api";
import { HOME } from "@/lib/session";

import { Loading } from "./AppShell";
import { usePublicStatus } from "./AuthForms";
import styles from "./landing.module.css";
import { BrandMark, Icon } from "./ui";
import ReliefIllustration from "./ui/ReliefIllustration";

const ROLES = [
  { role: "operator", icon: "shield", title: "Duty officers", text: "See every hazard, truck and plan as it changes, and approve each dispatch." },
  { role: "driver", icon: "truck", title: "Drivers", text: "Get a safe route in your language, read aloud, even through dead zones." },
  { role: "supplier", icon: "box", title: "Suppliers", text: "Report the stock you hold, so planners only promise what really exists." },
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
    <div className={styles.page}>
      <header className={styles.top}>
        <span className={styles.brand}><BrandMark size={32} /><span>SafePassage</span></span>
        <span className={styles.actions}>
          <Link className="button" href="/signin">Sign in</Link>
          <Link className="button primary" href="/signup">Create account</Link>
        </span>
      </header>

      <section className={styles.hero}>
        <div>
          <span className={styles.eyebrow}><i /> Live across Andhra Pradesh</span>
          <h1>Relief goods, on roads that are actually safe.</h1>
          <p>SafePassage turns live disaster alerts into delivery plans that avoid danger zones. People stay in charge: nothing is dispatched until a duty officer approves it.</p>
          <div className={styles.cta}>
            <Link className="button primary" href="/signup" style={{ minHeight: 52, padding: "0 26px", fontSize: 16 }}>Get started</Link>
            <Link className="button" href="/signin" style={{ minHeight: 52, padding: "0 26px", fontSize: 16 }}>Sign in</Link>
          </div>
        </div>
        <div className={styles.art}><ReliefIllustration /></div>
      </section>

      <dl className={styles.stats} aria-live="polite" aria-label="Live status">
        <div className={styles.stat}><dt>Active alerts</dt><dd>{status?.hazards_active ?? "–"}</dd></div>
        <div className={styles.stat}><dt>Severe or worse</dt><dd>{status?.hazards_severe ?? "–"}</dd></div>
        <div className={styles.stat}><dt>Live data sources</dt><dd>{status ? `${status.sources_fresh}/${status.sources_total}` : "–"}</dd></div>
      </dl>

      <section className={styles.roles} aria-label="Who it is for">
        {ROLES.map((r) => (
          <Link key={r.role} href={`/signup?role=${r.role}`} className={styles.role}>
            <span className={styles.roleIcon}><Icon name={r.icon} size={26} /></span>
            <h2>{r.title}</h2>
            <p>{r.text}</p>
            <span>Get started →</span>
          </Link>
        ))}
      </section>

      <section className={styles.promise}>
        <div className={styles.promiseCard}>
          <Icon name="shield" size={24} />
          <span><b>A person approves every dispatch.</b>Plans are checked against the current hazards again after approval, so a stale plan is never dispatched.</span>
        </div>
      </section>
    </div>
  );
}
