"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { X } from "lucide-react";

import { apiGet, apiPost, setToken } from "@/lib/api";
import { NAV } from "@/lib/modules";

interface Me { email: string; display_name?: string | null;
  roles?: { name: string }[] }

export function Sidebar({
  mobileOpen,
  onClose,
}: {
  mobileOpen: boolean;
  onClose: () => void;
}) {
  const pathname = usePathname();
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => {
    apiGet<Me>("/api/v1/auth/me").then(setMe).catch(() => setMe(null));
  }, []);
  const name = me?.display_name || me?.email || "Sign in";
  const initials = me ? (me.display_name ?? me.email)
    .split(/[\s@.]+/).filter(Boolean).slice(0, 2)
    .map((w) => w[0].toUpperCase()).join("") : "?";

  const body = (
    <>
      <Link
        href="/settings"
        onClick={onClose}
        className="mb-4 flex items-center gap-3 rounded-full border border-border-strong bg-surface-2 px-2.5 py-2 transition-colors hover:border-accent/40"
      >
        <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-brand to-[#f5a623] text-[13px] font-bold text-[#0b0f1a]">
          {initials}
        </span>
        <span className="flex flex-col leading-tight">
          <span className="text-sm font-semibold">{name}</span>
          <span className="text-[11px] text-dim">
            {me ? me.roles?.[0]?.name ?? "member" : "sign in for actions"}
          </span>
        </span>
      </Link>

      <nav aria-label="Primary" className="flex-1 space-y-3 overflow-y-auto">
        {NAV.map((group) => (
          <div key={group.label}>
            {(() => {
              const GroupIcon = group.modules[0].icon;
              return (
                <div className="flex items-center gap-2 px-2.5 py-1 text-[11px] font-bold tracking-widest text-dim uppercase">
                  <GroupIcon className="size-3.5 text-accent" aria-hidden />
                  {group.label}
                </div>
              );
            })()}
            <ul className="mt-0.5 space-y-px pl-3">
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
                      className={`flex items-center gap-2.5 border-l-2 py-1.5 pl-3 text-[13px] transition-all duration-200 ${
                        active
                          ? "border-accent font-semibold text-accent"
                          : "border-transparent text-dim hover:border-accent/60 hover:text-text"
                      }`}
                    >
                      <m.icon
                        className={`size-3.5 shrink-0 transition-colors ${
                          active ? "text-accent" : "text-faint"
                        }`}
                        aria-hidden
                      />
                      {m.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className="mt-4 border-t border-border pt-3">
        {me ? (
          <button
            onClick={async () => {
              try { await apiPost("/api/v1/auth/logout", {}); } catch {}
              setToken(null); setMe(null);
              window.location.href = "/settings";
            }}
            className="mb-2 w-full rounded-lg border border-border px-3 py-1.5 text-[11px] text-dim transition hover:border-neg/50 hover:text-neg"
          >
            Sign out
          </button>
        ) : (
          <Link href="/settings" onClick={onClose}
            className="mb-2 block w-full rounded-lg border border-accent/50 px-3 py-1.5 text-center text-[11px] font-semibold text-accent transition hover:bg-accent/10">
            Sign in
          </Link>
        )}
        <div className="text-center text-[11px] tracking-wider text-faint uppercase">
          VAIIP v0.1 · Internal
        </div>
      </div>
    </>
  );

  return (
    <>
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-border bg-[rgba(15,22,36,0.85)] px-2.5 py-5 backdrop-blur-xl lg:flex">
        <button
          onClick={onClose}
          aria-label="Close navigation"
          className="hidden"
        >
          <X className="size-4" />
        </button>
        {body}
      </aside>
      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={onClose}
            aria-hidden
          />
          <aside className="absolute top-0 left-0 flex h-full w-64 flex-col border-r border-border bg-surface-solid px-2.5 py-5">
            <button
              onClick={onClose}
              aria-label="Close navigation"
              className="mb-2 self-end rounded p-1 text-dim hover:text-text"
            >
              <X className="size-4" />
            </button>
            {body}
          </aside>
        </div>
      )}
    </>
  );
}
