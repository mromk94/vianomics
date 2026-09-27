"use client";

import { Bell, Menu, Moon, Search, Sun } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

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
        VAIIP <span className="font-light text-text">| Vianomics AI</span>
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

        <span className="relative rounded-full p-1.5 text-dim" title="Alerts">
          <Bell className="size-4" />
        </span>

        <div className="flex items-center gap-2 rounded-full border border-border-strong bg-surface-2 py-1 pr-3 pl-1">
          <span className="flex size-7 items-center justify-center rounded-full bg-gradient-to-br from-brand to-[#f5a623] text-[11px] font-bold text-[#0b0f1a]">
            DU
          </span>
          <span className="hidden text-[13px] md:block">Dere</span>
        </div>
      </div>
    </header>
  );
}
