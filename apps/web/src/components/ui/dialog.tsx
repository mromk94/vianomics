"use client";

import { X } from "lucide-react";
import { useEffect, useRef } from "react";

export function Dialog({
  open,
  onClose,
  title,
  children,
  wide = false,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onCancel = () => onClose();
    el.addEventListener("cancel", onCancel);
    return () => el.removeEventListener("cancel", onCancel);
  }, [onClose]);

  return (
    <dialog
      ref={ref}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
      className={`backdrop:bg-black/60 m-auto w-full rounded-md border border-border bg-surface p-0 text-text ${
        wide ? "max-w-3xl" : "max-w-lg"
      }`}
    >
      <header className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold">{title}</h2>
        <button
          onClick={onClose}
          aria-label="Close dialog"
          className="rounded p-1 text-dim hover:bg-surface-2 hover:text-text"
        >
          <X className="size-4" />
        </button>
      </header>
      <div className="p-4">{children}</div>
    </dialog>
  );
}
