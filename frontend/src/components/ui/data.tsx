"use client";

// Finding things: search, filter chips, and a table that stays readable.

import { ReactNode } from "react";

import Icon from "./icons";
import styles from "./ui.module.css";

export function FilterBar({ children }: { children: ReactNode }) {
  return <div className={styles.filterBar}>{children}</div>;
}

export function SearchInput({ value, onChange, placeholder, label = "Search" }: { value: string; onChange: (value: string) => void; placeholder: string; label?: string }) {
  return (
    <label className={styles.search}>
      <Icon name="search" size={18} />
      <input type="search" aria-label={label} placeholder={placeholder} value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

export function FilterChips<T extends string>({ options, value, onChange, label }: {
  options: { key: T; label: string; count?: number }[];
  value: T;
  onChange: (key: T) => void;
  label: string;
}) {
  return (
    <div className={styles.chips} role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.key} type="button" className={`${styles.chip} ${value === o.key ? styles.chipOn : ""}`} aria-pressed={value === o.key} onClick={() => onChange(o.key)}>
          {o.label}{o.count !== undefined && <small>{o.count}</small>}
        </button>
      ))}
    </div>
  );
}

export interface Column<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  numeric?: boolean;
}

export function DataTable<T>({ columns, rows, rowKey, onRowClick, selectedKey, empty, caption }: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  selectedKey?: string | null;
  empty?: ReactNode;
  caption: string;
}) {
  if (rows.length === 0 && empty) return <>{empty}</>;
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <caption className="sr-only" style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" }}>{caption}</caption>
        <thead>
          <tr>{columns.map((c) => <th key={c.key} scope="col" className={c.numeric ? styles.numCell : undefined}>{c.header}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const key = rowKey(row);
            const cls = `${onRowClick ? styles.rowClickable : ""} ${selectedKey === key ? styles.rowSelected : ""}`;
            return (
              <tr key={key} className={cls} onClick={onRowClick ? () => onRowClick(row) : undefined}>
                {columns.map((c) => <td key={c.key} className={c.numeric ? styles.numCell : undefined}>{c.render(row)}</td>)}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
