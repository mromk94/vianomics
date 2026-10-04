"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { apiGet } from "@/lib/api";
import { ALL_MODULES } from "@/lib/modules";

interface Hit { id: string; symbol: string; name: string;
  asset_class: string }

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const [tickers, setTickers] = useState<Hit[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const debRef = useRef<ReturnType<typeof setTimeout>>(null);
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
      setTickers([]);
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  // debounced instrument lookup — tickers sit above module results
  useEffect(() => {
    if (debRef.current) clearTimeout(debRef.current);
    const q = query.trim();
    if (!q) { setTickers([]); return; }
    debRef.current = setTimeout(() => {
      apiGet<Hit[]>(
        `/api/v1/universe/instruments?q=${encodeURIComponent(q)}&limit=6`)
        .then(setTickers).catch(() => setTickers([]));
    }, 200);
    return () => { if (debRef.current) clearTimeout(debRef.current); };
  }, [query]);

  const modules = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return ALL_MODULES;
    return ALL_MODULES.filter(
      (m) =>
        m.label.toLowerCase().includes(q) ||
        m.slug.includes(q) ||
        m.parts.join(" ").toLowerCase().includes(q),
    );
  }, [query]);

  // flat list for keyboard nav — tickers first
  const flat = useMemo(
    () => [
      ...tickers.map((t) => ({ kind: "t" as const, t })),
      ...modules.map((m) => ({ kind: "m" as const, m })),
    ],
    [tickers, modules],
  );

  const go = (item: (typeof flat)[number]) => {
    setOpen(false);
    router.push(item.kind === "t"
      ? `/symbol/${item.t.symbol}` : `/${item.m.slug}`);
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
        className="w-[94vw] max-w-lg overflow-hidden rounded-md border border-border-strong bg-surface shadow-2xl"
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
              setIndex((i) => Math.min(i + 1, flat.length - 1));
            }
            if (e.key === "ArrowUp") {
              e.preventDefault();
              setIndex((i) => Math.max(i - 1, 0));
            }
            if (e.key === "Enter" && flat[index]) go(flat[index]);
          }}
          placeholder="Search ticker or jump to module…"
          className="w-full border-b border-border bg-transparent px-4 py-3 text-sm text-text placeholder:text-faint focus:outline-none"
        />
        <ul className="max-h-72 overflow-y-auto py-1">
          {flat.length === 0 && (
            <li className="px-4 py-3 text-sm text-faint">No matches</li>
          )}
          {flat.map((item, i) =>
            item.kind === "t" ? (
              <li key={`t-${item.t.id}`}>
                <button
                  onClick={() => go(item)}
                  onMouseEnter={() => setIndex(i)}
                  className={`flex w-full items-center gap-3 px-4 py-2 text-left text-sm ${
                    i === index ? "bg-surface-2 text-text" : "text-dim"
                  }`}
                >
                  <span className="num w-16 shrink-0 font-bold text-accent">{item.t.symbol}</span>
                  <span className="truncate">{item.t.name}</span>
                  <span className="ml-auto text-[10px] text-faint">{item.t.asset_class}</span>
                </button>
              </li>
            ) : (
              <li key={`m-${item.m.slug || "root"}`}>
                <button
                  onClick={() => go(item)}
                  onMouseEnter={() => setIndex(i)}
                  className={`flex w-full items-center gap-3 px-4 py-2 text-left text-sm ${
                    i === index ? "bg-surface-2 text-text" : "text-dim"
                  }`}
                >
                  <item.m.icon className="size-4 shrink-0" aria-hidden />
                  <span>{item.m.label}</span>
                  <span className="ml-auto text-[10px] text-faint">
                    {item.m.parts.join(" · ")}
                  </span>
                </button>
              </li>
            ),
          )}
        </ul>
      </div>
    </div>
  );
}
