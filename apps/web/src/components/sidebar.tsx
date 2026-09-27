"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { X } from "lucide-react";

import { NAV } from "@/lib/modules";

export function Sidebar({
  mobileOpen,
  onClose,
}: {
  mobileOpen: boolean;
  onClose: () => void;
}) {
  const pathname = usePathname();

  const body = (
    <>
      <div className="mb-4 flex items-center justify-between px-2">
        <Link
          href="/"
          className="flex items-center gap-2"
          onClick={onClose}
          aria-label="VAIIP home"
        >
          <span className="flex size-7 items-center justify-center rounded bg-brand text-[13px] font-bold text-black">
            V
          </span>
          <span className="leading-tight">
            <span className="block text-sm font-semibold tracking-wide">
              VIANOMICS
            </span>
            <span className="block text-[10px] tracking-wider text-faint uppercase">
              VAIIP · Trader OS
            </span>
          </span>
        </Link>
        <button
          onClick={onClose}
          aria-label="Close navigation"
          className="rounded p-1 text-dim hover:text-text lg:hidden"
        >
          <X className="size-4" />
        </button>
      </div>

      <nav aria-label="Primary" className="flex-1 space-y-4 overflow-y-auto">
        {NAV.map((group) => (
          <div key={group.label}>
            <div className="px-3 pb-1 text-[10px] font-semibold tracking-widest text-faint uppercase">
              {group.label}
            </div>
            <ul className="space-y-0.5">
              {group.modules.map((m) => {
                const href = `/${m.slug}`;
                const active =
                  m.slug === "" ? pathname === "/" : pathname === href;
                return (
                  <li key={m.slug || "root"}>
                    <Link
                      href={href}
                      onClick={onClose}
                      aria-current={active ? "page" : undefined}
                      className={`flex items-center gap-2.5 rounded px-3 py-1.5 text-[13px] transition-colors ${
                        active
                          ? "bg-surface-3 font-medium text-text"
                          : "text-dim hover:bg-surface-2 hover:text-text"
                      }`}
                    >
                      <m.icon className="size-4 shrink-0" aria-hidden />
                      {m.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className="mt-4 border-t border-border px-3 pt-3 text-[10px] tracking-wider text-faint uppercase">
        VAIIP v0.1 · Internal
      </div>
    </>
  );

  return (
    <>
      {/* Desktop / tablet rail */}
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-border bg-surface px-2 py-4 lg:flex">
        {body}
      </aside>
      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div
            className="absolute inset-0 bg-black/60"
            onClick={onClose}
            aria-hidden
          />
          <aside className="absolute top-0 left-0 flex h-full w-64 flex-col border-r border-border bg-surface px-2 py-4">
            {body}
          </aside>
        </div>
      )}
    </>
  );
}
