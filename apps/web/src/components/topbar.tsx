"use client";

import { Bell, Command, Menu, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

export function Topbar({ onMenu }: { onMenu: () => void }) {
  const [theme, setTheme] = useState<"dark" | "light">("dark");

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

  return (
    <header className="sticky top-0 z-30 flex h-12 items-center gap-3 border-b border-border bg-bg/95 px-4 backdrop-blur">
      <button
        onClick={onMenu}
        aria-label="Open navigation"
        className="rounded p-1.5 text-dim hover:bg-surface-2 hover:text-text lg:hidden"
      >
        <Menu className="size-4" />
      </button>

      <button
        onClick={() =>
          window.dispatchEvent(
            new KeyboardEvent("keydown", { key: "k", metaKey: true }),
          )
        }
        className="hidden items-center gap-2 rounded border border-border bg-surface-2 px-2.5 py-1.5 text-xs text-faint hover:text-dim sm:flex"
      >
        <Command className="size-3.5" />
        <span>Jump to module</span>
        <kbd className="rounded border border-border px-1 text-[10px]">⌘K</kbd>
      </button>

      <div className="ml-auto flex items-center gap-1">
        <button
          onClick={toggleTheme}
          aria-label="Toggle theme"
          className="rounded p-1.5 text-dim hover:bg-surface-2 hover:text-text"
        >
          {theme === "dark" ? (
            <Sun className="size-4" />
          ) : (
            <Moon className="size-4" />
          )}
        </button>
        <span className="relative rounded p-1.5 text-dim" title="Alerts">
          <Bell className="size-4" />
        </span>
        <div className="ml-1 flex items-center gap-2 rounded-full border border-border bg-surface-2 py-1 pr-3 pl-1">
          <span className="flex size-6 items-center justify-center rounded-full bg-brand text-[11px] font-bold text-black">
            DU
          </span>
          <span className="hidden text-xs text-dim md:block">Dere U.</span>
        </div>
      </div>
    </header>
  );
}
