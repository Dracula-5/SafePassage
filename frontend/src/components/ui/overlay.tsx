"use client";

// Dialogs and drawers on the browser's own <dialog>: focus stays inside, Esc closes, and the page behind
// cannot be clicked. Used instead of window.confirm and window.prompt.

import { ReactNode, useEffect, useRef } from "react";

import Icon from "./icons";
import styles from "./ui.module.css";

function useDialog(open: boolean, onClose: () => void) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return {
    ref,
    // Esc and the backdrop both end here, so the owner's state never disagrees with the dialog.
    onCancel: (e: React.SyntheticEvent) => {
      e.preventDefault();
      onClose();
    },
    onClick: (e: React.MouseEvent<HTMLDialogElement>) => {
      if (e.target === e.currentTarget) onClose();
    },
  };
}

export function ConfirmDialog({ open, title, children, confirmLabel = "Confirm", danger, busy, onConfirm, onCancel }: {
  open: boolean;
  title: string;
  children?: ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const dialog = useDialog(open, onCancel);
  return (
    <dialog className={styles.dialog} aria-labelledby="confirm-title" {...dialog}>
      <div className={styles.dialogBody}>
        <h2 id="confirm-title">{title}</h2>
        {children}
        <div className={styles.dialogActions}>
          <button type="button" onClick={onCancel}>Cancel</button>
          <button type="button" className={danger ? "danger solid" : "primary"} disabled={busy} onClick={onConfirm}>{busy ? "Working…" : confirmLabel}</button>
        </div>
      </div>
    </dialog>
  );
}

export function DetailDrawer({ open, title, onClose, children }: { open: boolean; title: string; onClose: () => void; children: ReactNode }) {
  const dialog = useDialog(open, onClose);
  return (
    <dialog className={`${styles.dialog} ${styles.drawer}`} aria-labelledby="drawer-title" {...dialog}>
      <div className={styles.drawerHead}>
        <h2 id="drawer-title">{title}</h2>
        <button type="button" className={styles.iconBtn} aria-label="Close" onClick={onClose}><Icon name="close" /></button>
      </div>
      <div className={styles.drawerBody}>{children}</div>
    </dialog>
  );
}
