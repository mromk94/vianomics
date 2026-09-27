"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { ALL_MODULES } from "@/lib/modules";

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (open) {
      setQuery("");
      setIndex(0);
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return ALL_MODULES;
    return ALL_MODULES.filter(
      (m) =>
        m.label.toLowerCase().includes(q) ||
        m.slug.includes(q) ||
        m.parts.join(" ").toLowerCase().includes(q),
    );
  }, [query]);

  const go = (slug: string) => {
    setOpen(false);
    router.push(`/${slug}`);
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 pt-[15vh]"
      onClick={() => setOpen(false)}
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
    >
      <div
        className="w-full max-w-lg overflow-hidden rounded-md border border-border-strong bg-surface shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setIndex(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") setOpen(false);
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setIndex((i) => Math.min(i + 1, results.length - 1));
            }
            if (e.key === "ArrowUp") {
              e.preventDefault();
              setIndex((i) => Math.max(i - 1, 0));
            }
            if (e.key === "Enter" && results[index]) go(results[index].slug);
          }}
          placeholder="Jump to module…"
          className="w-full border-b border-border bg-transparent px-4 py-3 text-sm text-text placeholder:text-faint focus:outline-none"
        />
        <ul className="max-h-72 overflow-y-auto py-1">
          {results.length === 0 && (
            <li className="px-4 py-3 text-sm text-faint">No module found</li>
          )}
          {results.map((m, i) => (
            <li key={m.slug || "root"}>
              <button
                onClick={() => go(m.slug)}
                onMouseEnter={() => setIndex(i)}
                className={`flex w-full items-center gap-3 px-4 py-2 text-left text-sm ${
                  i === index ? "bg-surface-2 text-text" : "text-dim"
                }`}
              >
                <m.icon className="size-4 shrink-0" aria-hidden />
                <span>{m.label}</span>
                <span className="ml-auto text-[10px] text-faint">
                  {m.parts.join(" · ")}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
