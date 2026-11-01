// Stock the supplier has reported: cards on a phone, a table on a desktop. Only reported figures are shown.

import { StockLevel } from "@/lib/api";
import { ago, itemName } from "@/lib/format";

import Icon, { itemIcon } from "./icons";
import { quantityText } from "./orders";
import styles from "./supplier.module.css";
import { EmptyState } from "./ui";

export default function InventoryList({ stock, units, desktop, compact, onPick }: {
  stock: StockLevel[];
  units: Record<string, string>;
  desktop: boolean;
  /** Short rows for "Recently Reported": name, quantity and when. */
  compact?: boolean;
  onPick?: (item: string) => void;
}) {
  if (stock.length === 0) {
    return compact
      ? <p className={styles.emptyInline}>Nothing reported yet.</p>
      : <EmptyState icon="boxes" title="No stock reported yet">Add what you hold, and planners can use it straight away.</EmptyState>;
  }

  if (desktop && !compact) {
    return (
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr><th>Item</th><th className={styles.num}>Held</th><th className={styles.num}>Reserved</th><th className={styles.num}>Available</th><th>Reported</th></tr>
          </thead>
          <tbody>
            {stock.map((s) => (
              <tr key={s.item_code}>
                <td><b>{itemName(s.item_code)}</b> <span className={styles.muted}>{units[s.item_code]}</span></td>
                <td className={styles.num}>{s.quantity.toLocaleString()}</td>
                <td className={styles.num}>{s.reserved.toLocaleString()}</td>
                <td className={styles.num}><b>{s.available.toLocaleString()}</b></td>
                <td className={styles.muted}>{ago(s.reported_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <ul className={styles.stack}>
      {stock.map((s) => {
        const unit = units[s.item_code];
        const body = (
          <>
            <span className={`${styles.bubble} ${s.item_code.startsWith("WATER") ? "" : styles.bubbleAmber}`}><Icon name={itemIcon(s.item_code)} /></span>
            <span className={styles.itemBody}>
              <b>{itemName(s.item_code)}{unit ? ` (${unit})` : ""}</b>
              {compact
                ? <span>{quantityText(s.quantity, unit)} · {ago(s.reported_at)}</span>
                : <span className={styles.itemSplit}>
                    <span>Reserved {s.reserved.toLocaleString()}</span>
                    <span>Reported {ago(s.reported_at)}</span>
                  </span>}
            </span>
            {!compact && <span className={styles.itemQty}>{s.available.toLocaleString()}<small>available of {s.quantity.toLocaleString()}</small></span>}
            {onPick && <span className={styles.chev}><Icon name="chevron" size={18} /></span>}
          </>
        );
        return (
          <li key={s.item_code}>
            {onPick
              ? <button type="button" className={styles.itemCard} onClick={() => onPick(s.item_code)}>{body}</button>
              : <div className={styles.itemCard}>{body}</div>}
          </li>
        );
      })}
    </ul>
  );
}
