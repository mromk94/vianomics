"use client";

import { Bell, Menu, Moon, Search, Sun } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { apiGet } from "@/lib/api";

function UserChip() {
  const [me, setMe] = useState<{ email: string; display_name?: string | null } | null>(null);
  useEffect(() => {
    const fetch = () =>
      apiGet<{ email: string; display_name?: string | null }>("/api/v1/auth/me")
        .then(setMe).catch(() => setMe(null));
    fetch();
    window.addEventListener("vaiip-auth", fetch);
    return () => window.removeEventListener("vaiip-auth", fetch);
  }, []);
  const initials = me ? (me.display_name ?? me.email)
    .split(/[\s@.]+/).filter(Boolean).slice(0, 2)
    .map((w) => w[0]!.toUpperCase()).join("") : "?";
  return (
    <Link href="/settings" title={me ? me.email : "Sign in"}
      className="flex items-center gap-2 rounded-full border border-border-strong bg-surface-2 py-1 pr-3 pl-1 transition-colors hover:border-accent/40">
      <span className="flex size-7 items-center justify-center rounded-full bg-gradient-to-br from-brand to-[#f5a623] text-[11px] font-bold text-[#0b0f1a]">
        {initials}
      </span>
      <span className="hidden max-w-40 truncate text-[13px] md:block">
        {me ? me.display_name ?? me.email : "Sign in"}
      </span>
    </Link>
  );
}

const QUICK_LINKS = [
  { href: "/", label: "Watchlist", slug: "universe" },
  { href: "/portfolio", label: "Portfolio" },
  { href: "/screener", label: "Screener" },
  { href: "/committee", label: "Committee" },
];

export function Topbar({ onMenu }: { onMenu: () => void }) {
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const pathname = usePathname();

  useEffect(() => {
    const saved = window.localStorage.getItem("vaiip-theme");
    if (saved === "light") {
      setTheme("light");
      document.documentElement.dataset.theme = "light";
    }
  }, []);

  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.dataset.theme = next === "light" ? "light" : "";
    window.localStorage.setItem("vaiip-theme", next);
  };

  const openPalette = () =>
    window.dispatchEvent(
      new KeyboardEvent("keydown", { key: "k", metaKey: true }),
    );

  return (
    <header className="sticky top-0 z-30 mb-5 flex flex-wrap items-center gap-4 border-b border-border bg-[rgba(7,11,20,0.8)] px-4 py-2.5 backdrop-blur-xl">
      <button
        onClick={onMenu}
        aria-label="Open navigation"
        className="rounded p-1.5 text-dim hover:text-text lg:hidden"
      >
        <Menu className="size-4" />
      </button>

      <Link href="/" className="mr-2 text-lg font-bold text-accent">
        VAIIP <span className="font-light text-text">| Vesturs AI</span>
      </Link>

      <nav className="hidden items-center gap-1 md:flex" aria-label="Quick links">
        {QUICK_LINKS.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className={`rounded-full px-3.5 py-1.5 text-[13px] font-medium transition-colors ${
              pathname === l.href
                ? "bg-surface-3 text-accent"
                : "text-dim hover:bg-surface-2 hover:text-accent"
            }`}
          >
            {l.label}
          </Link>
        ))}
      </nav>

      <div className="ml-auto flex items-center gap-3">
        <button
          onClick={openPalette}
          className="flex items-center gap-2 rounded-full border border-border-strong bg-surface-2 px-4 py-1.5 text-[13px] text-faint transition-colors hover:border-accent/50 hover:text-dim"
        >
          <Search className="size-3.5" />
          <span className="hidden sm:inline">Search ticker…</span>
          <kbd className="hidden rounded border border-border px-1 text-[10px] sm:inline">
            ⌘K
          </kbd>
        </button>

        <button
          onClick={toggleTheme}
          aria-label="Toggle theme"
          className="rounded-full p-1.5 text-dim transition-colors hover:text-accent"
        >
          {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
        </button>

        <Link href="/monitoring" className="relative rounded-full p-1.5 text-dim hover:text-accent" title="Alerts">
          <Bell className="size-4" />
        </Link>

        <UserChip />
      </div>
    </header>
  );
}
