"use client";

// Who this supplier is and whether a duty officer has verified them. Everything here is read-only: the API has no profile update.

import { Account, SupplierProfile as Profile } from "@/lib/api";
import { signOut } from "@/lib/session";

import Icon from "./icons";
import styles from "./supplier.module.css";
import { Notice, PageHeader, UNVERIFIED_NOTE, VerificationBadge } from "./ui";

function Info({ icon, label, value }: { icon: string; label: string; value: string }) {
  return (
    <li className={styles.infoRow}>
      <span className={styles.bubble}><Icon name={icon} size={20} /></span>
      <div><small>{label}</small><b>{value}</b></div>
    </li>
  );
}

export default function SupplierProfile({ account, profile }: { account: Account; profile: Profile }) {
  return (
    <>
      <PageHeader title="Profile" backTo={{ name: "home" }} backMobileOnly />
      <div className={styles.profileGrid}>
        <div className={styles.stack}>
          <div className={styles.profileHero}>
            <span className={styles.avatar}><Icon name="building" size={36} /></span>
            <h2>{account.name}</h2>
            <p>{profile.name} · {profile.site}</p>
            <VerificationBadge verified={profile.verified} />
          </div>
          <button type="button" className={styles.dangerBtn} onClick={signOut}><Icon name="logout" size={20} /> Logout</button>
        </div>

        <div className={styles.stack} style={{ gap: 16 }}>
          <section className={styles.card} aria-labelledby="org-h">
            <h2 id="org-h">Organisation Details</h2>
            <ul>
              <Info icon="building" label="Organisation" value={profile.name} />
              <Info icon="pin" label="Warehouse or site" value={profile.site} />
              <Info icon="user" label="Account holder" value={account.name} />
            </ul>
          </section>

          <section className={styles.card} aria-labelledby="ver-h">
            <h2 id="ver-h">Verification Status</h2>
            {profile.verified
              ? <Notice tone="good">Your organisation is verified. Plans that use your stock no longer need an extra duty-officer check for that reason.</Notice>
              : <Notice tone="warn">Awaiting Verification. {UNVERIFIED_NOTE} Only a duty officer can verify you; there is nothing to submit here.</Notice>}
          </section>

          <section className={styles.card} aria-labelledby="contact-h">
            <h2 id="contact-h">Contact Information</h2>
            <ul>
              <Info icon="mail" label="Sign-in email" value={account.email} />
            </ul>
            <p className={`${styles.muted} ${styles.small}`}>Phone and WhatsApp numbers given at registration are used by the planning team and are not shown back here.</p>
          </section>

          <section className={styles.card} aria-labelledby="support-h">
            <h2 id="support-h">Support</h2>
            <ul>
              <Info icon="headset" label="Need help?" value="Ask your duty officer. They approve plans and verify organisations." />
            </ul>
          </section>
        </div>
      </div>
    </>
  );
}
