"use client";

import { Dialog } from "./dialog";

export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  body,
  confirmLabel = "Confirm",
  destructive = false,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  body: React.ReactNode;
  confirmLabel?: string;
  destructive?: boolean;
}) {
  return (
    <Dialog open={open} onClose={onClose} title={title}>
      <div className="text-sm text-dim">{body}</div>
      <div className="mt-4 flex justify-end gap-2">
        <button
          onClick={onClose}
          className="rounded border border-border bg-surface-2 px-3 py-1.5 text-xs font-medium hover:bg-surface-3"
        >
          Cancel
        </button>
        <button
          onClick={onConfirm}
          className={`rounded px-3 py-1.5 text-xs font-semibold text-white ${
            destructive ? "bg-neg hover:opacity-90" : "bg-accent hover:opacity-90"
          }`}
        >
          {confirmLabel}
        </button>
      </div>
    </Dialog>
  );
}
