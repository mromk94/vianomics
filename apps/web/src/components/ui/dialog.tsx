"use client";

import { X } from "lucide-react";
import { useEffect, useRef } from "react";

export function Dialog({
  open,
  onClose,
  title,
  children,
  wide = false,
  size,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  wide?: boolean;
  size?: "md" | "lg" | "xl" | "2xl" | "full";
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

  const sz = size ?? (wide ? "lg" : "md");
  const maxW = sz === "full" ? "max-w-[96vw]"
    : sz === "2xl" ? "max-w-7xl"
    : sz === "xl" ? "max-w-5xl"
    : sz === "lg" ? "max-w-3xl" : "max-w-lg";

  return (
    <dialog
      ref={ref}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
      /* opaque bg — content must stay readable over the page; nearly
         full-viewport on mobile */
      className={`backdrop:bg-black/75 m-auto h-fit max-h-[94dvh] w-[96vw] overflow-y-auto rounded-md border border-border bg-[#0b0f1a] p-0 text-text shadow-2xl ${maxW}`}
    >
      <header className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-[#0b0f1a] px-4 py-3">
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
