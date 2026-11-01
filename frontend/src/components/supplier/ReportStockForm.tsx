"use client";

// Report what is physically held. This REPLACES the last figure for the item (supplierApi.report), it never adds to it.

import { FormEvent, useState } from "react";

import { supplierApi, SupplierProfile } from "@/lib/api";
import { itemName } from "@/lib/format";

import Icon from "./icons";
import InventoryList from "./InventoryList";
import { go } from "./nav";
import { quantityText } from "./orders";
import styles from "./supplier.module.css";
import { Notice, PageHeader } from "./ui";

export default function ReportStockForm({ profile, desktop, onSaved }: { profile: SupplierProfile; desktop: boolean; onSaved: () => Promise<void> }) {
  const units = Object.fromEntries(profile.items.map((i) => [i.code, i.unit]));
  const [item, setItem] = useState(profile.items[0]?.code ?? "");
  const [quantity, setQuantity] = useState("");
  const [weight, setWeight] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const current = profile.stock.find((s) => s.item_code === item);
  const recent = [...profile.stock].sort((a, b) => b.reported_at.localeCompare(a.reported_at)).slice(0, 3);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const amount = Number(quantity);
    const perUnit = Number(weight);
    if (!item) return setMsg({ ok: false, text: "Choose an item." });
    if (quantity.trim() === "" || !Number.isFinite(amount) || amount < 0) return setMsg({ ok: false, text: "Enter the quantity you hold: zero or more." });
    if (weight.trim() === "" || !Number.isFinite(perUnit) || perUnit <= 0) return setMsg({ ok: false, text: "Enter the weight of one unit in kg: more than zero." });
    setSaving(true);
    setMsg(null);
    try {
      await supplierApi.report([{ item_code: item, quantity: amount, unit_weight_kg: perUnit }]);
      setMsg({ ok: true, text: `${itemName(item)} updated. Planners can see it now.` });
      setQuantity("");
      await onSaved();
    } catch (err) {
      setMsg({ ok: false, text: (err as Error).message });
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <PageHeader title="Report Stock" backTo={{ name: "inventory" }} backMobileOnly />
      <div className={styles.twoCol}>
        <div className={styles.stack}>
          <div className={styles.banner}>
            <span className={styles.bannerIcon}><Icon name="boxes" size={30} /></span>
            <div>
              <h2>Update Your Stock</h2>
              <p>Report what you actually hold for ongoing and upcoming operations. This replaces the last figure for that item.</p>
            </div>
          </div>
          <form className={`${styles.card} ${styles.form}`} onSubmit={submit} noValidate>
            <label className={styles.field}>Item
              <select className={styles.control} value={item} onChange={(e) => { setItem(e.target.value); setMsg(null); }}>
                {profile.items.map((i) => <option key={i.code} value={i.code}>{itemName(i.code)} ({i.unit})</option>)}
              </select>
              {current && <span className={styles.fieldHint}>Currently reported: {quantityText(current.quantity, units[item])}, {current.unit_weight_kg} kg each.</span>}
            </label>
            <div className={styles.fieldRow}>
              <label className={styles.field}>Quantity
                <input className={styles.control} type="number" min={0} step="any" inputMode="decimal" placeholder="e.g., 5,000" required
                  value={quantity} onChange={(e) => setQuantity(e.target.value)} />
              </label>
              <label className={styles.field}>Weight per unit (kg)
                <input className={styles.control} type="number" min={0.001} step="any" inputMode="decimal" placeholder="e.g., 1" required
                  value={weight} onChange={(e) => setWeight(e.target.value)} />
              </label>
            </div>
            {msg && <Notice tone={msg.ok ? "good" : "bad"}>{msg.text}</Notice>}
            <div className={styles.formActions}>
              <button className={styles.primaryBtn} disabled={saving}><Icon name="save" size={20} /> {saving ? "Saving…" : "Save Stock"}</button>
            </div>
          </form>
        </div>

        <section className={styles.card} aria-labelledby="recent-h">
          <div className={styles.sectionHead}>
            <h2 id="recent-h">Recently Reported</h2>
            {profile.stock.length > 0 && <button type="button" className={styles.linkBtn} onClick={() => go({ name: "inventory" })}>View All</button>}
          </div>
          <InventoryList stock={recent} units={units} desktop={desktop} compact onPick={setItem} />
        </section>
      </div>
    </>
  );
}
