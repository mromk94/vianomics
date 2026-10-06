"use client";

import { X } from "lucide-react";
import { useEffect } from "react";

export function Drawer({
  open,
  onClose,
  title,
  wide = false,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  /** wide renders a workstation-depth panel for evidence-heavy
      detail (screener review, dossiers) instead of the compact rail */
  wide?: boolean;
  children: React.ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true">
      <div
        className="absolute inset-0 bg-black/60"
        onClick={onClose}
        aria-hidden
      />
      <aside className={`absolute top-0 right-0 h-full w-full border-l border-border bg-surface shadow-xl ${wide ? "sm:max-w-3xl xl:max-w-4xl" : "max-w-md"}`}>
        <header className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          <button
            onClick={onClose}
            aria-label="Close panel"
            className="rounded p-1 text-dim hover:bg-surface-2 hover:text-text"
          >
            <X className="size-4" />
          </button>
        </header>
        <div className="h-[calc(100%-49px)] overflow-y-auto p-4">{children}</div>
      </aside>
    </div>
  );
}
