"use client";

import { useEffect, useState } from "react";
import { X } from "lucide-react";

import { apiGet } from "@/lib/api";
import { fmtNum, fmtTime } from "@/lib/format";
import { StatusBadge } from "@/components/ui/status-badge";

export interface SymbolDetail {
  symbol: string; in_master: boolean; name: string | null;
  sector: string | null; exchange: string | null;
  asset_class: string | null;
  position: {
    market_value: number; quantity: number; avg_cost: number;
    unrealized: number | null; source?: string;
    external?: boolean; display_symbol?: string; sector?: string;
  } | null;
  screening: { score: number; verdict: string; at: string | null } | null;
  decision: { verdict: string; at: string; confidence: number | null;
    numbers: Record<string, unknown> } | null;
  technical: { decision: string; mean_reversion: string;
    trend_following: string; last_close: number | null;
    atr_14: number | null; data_fresh: boolean;
    freshness_note: string | null } | null;
  valuation: { sticker: number | null; buy: number | null;
    fair: number | null; engine: string; at: string | null } | null;
  dossier: { version: number; at: string | null;
    sections: number } | null;
}

// ── tiny pub/sub: any component opens the drawer without prop drilling
export function openSymbol(sym: string) {
  window.dispatchEvent(new CustomEvent("vaiip:symbol", { detail: sym }));
}

/** Clickable ticker — use everywhere a symbol renders. */
export function Sym({ s, className }: { s: string; className?: string }) {
  return (
    <button
      onClick={(e) => { e.stopPropagation(); openSymbol(s); }}
      className={`font-semibold text-accent hover:underline ${className ?? ""}`}>
      {s}
    </button>
  );
}

export function SymbolDrawer() {
  const [sym, setSym] = useState<string | null>(null);
  const [d, setD] = useState<SymbolDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  interface NewsItem { source: string; title: string; url: string | null;
    at: string | null; summary: string | null }
  const [news, setNews] = useState<{ items: NewsItem[];
    sources: string[] } | null>(null);

  useEffect(() => {
    const on = (e: Event) =>
      setSym((e as CustomEvent<string>).detail);
    window.addEventListener("vaiip:symbol", on);
    return () => window.removeEventListener("vaiip:symbol", on);
  }, []);

  useEffect(() => {
    if (!sym) { setD(null); setErr(null); return; }
    setD(null); setErr(null);
    apiGet<SymbolDetail>(`/api/v1/symbol/${sym}`)
      .then(setD).catch((e) => setErr(e.message));
    setNews(null);
    apiGet<{ items: NewsItem[]; sources: string[] }>(`/api/v1/news/${sym}`).then(setNews).catch(() => setNews({ items: [], sources: [] }));
  }, [sym]);

  useEffect(() => {
    if (!sym) return;
    const esc = (e: KeyboardEvent) =>
      e.key === "Escape" && setSym(null);
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [sym]);

  if (!sym) return null;

  const tone = (v?: string | null) =>
    v === "entry_signal" || v === "pass" || v === "approve_pending_human"
      ? "pos" : v === "fail" || v === "no_trade" || v === "reject"
        ? "neg" : "info" as const;

  return (
    <div className="fixed inset-0 z-[90] flex justify-end"
      onClick={() => setSym(null)}>
      <div className="absolute inset-0 bg-black/60 backdrop-blur-[2px]" />
      <aside onClick={(e) => e.stopPropagation()}
        className="relative z-10 flex h-full w-full max-w-md flex-col overflow-y-auto border-l border-border bg-[#0b0f1a] shadow-2xl sm:max-w-lg">
        {/* header */}
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-[#0b0f1a] px-4 py-3">
          <div>
            <span className="text-[17px] font-bold text-accent">{sym}</span>
            {d?.name && <span className="ml-2 text-[13px] text-dim">{d.name}</span>}
            <div className="text-[11px] text-faint">
              {[d?.sector, d?.exchange, d?.asset_class].filter(Boolean).join(" · ") || (d ? "not in security master" : "…")}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <a href={`/symbol/${sym}`}
              className="rounded-full border border-border px-2.5 py-0.5 text-[10px] text-dim hover:text-text">
              full page →
            </a>
            <button onClick={() => setSym(null)} className="rounded-full p-1.5 text-dim hover:text-text">
              <X className="size-4" />
            </button>
          </div>
        </div>

        {err && <div className="m-4 rounded-lg border border-warn/40 p-3 text-[12px] text-warn">{err}</div>}

        {/* chart — TradingView embedded, symbol-locked */}
        <div className="border-b border-border">
          <iframe key={sym} title={`${sym} chart`}
            src={`https://www.tradingview.com/widgetembed/?symbol=${sym}&interval=D&theme=dark&style=1&timezone=Etc%2FUTC&withdateranges=1&hide_side_toolbar=1&allow_symbol_change=0&studies=%5B%22RSI%40tv-basicstudies%22%5D`}
            className="h-[280px] w-full border-0" allow="fullscreen" />
        </div>

        <div className="flex-1 space-y-3 p-4 text-[13px]">
          {d?.position && (
            <section className="glass-tile p-3">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-dim">
                Held position{d.position.source ? ` · ${d.position.source}` : ""}
              </div>
              <div className="num mt-1.5 grid grid-cols-4 gap-2 text-[12px]">
                <div><div className="text-[10px] text-faint">QTY</div>{fmtNum(d.position.quantity, 0)}</div>
                <div><div className="text-[10px] text-faint">AVG</div>${fmtNum(d.position.avg_cost, 2)}</div>
                <div><div className="text-[10px] text-faint">VALUE</div>${fmtNum(d.position.market_value)}</div>
                <div><div className="text-[10px] text-faint">UNREAL.</div>
                  <span className={(d.position.unrealized ?? 0) >= 0 ? "text-pos" : "text-neg"}>
                    {d.position.unrealized != null ? `${d.position.unrealized >= 0 ? "+" : ""}$${fmtNum(d.position.unrealized)}` : "—"}
                  </span></div>
              </div>
            </section>
          )}

          <section className="grid grid-cols-2 gap-2">
            <div className="glass-tile p-3">
              <div className="text-[10px] uppercase tracking-wider text-dim">Green Zone</div>
              {d?.screening ? (
                <div className="mt-1 flex items-baseline gap-2">
                  <span className="num text-[18px] font-bold">{d.screening.score}<span className="text-[11px] text-faint">/20</span></span>
                  <StatusBadge tone={tone(d.screening.verdict)}>{d.screening.verdict}</StatusBadge>
                </div>
              ) : <div className="mt-1 text-[11px] text-faint">no screen run</div>}
            </div>
            <div className="glass-tile p-3">
              <div className="text-[10px] uppercase tracking-wider text-dim">Technical</div>
              {d?.technical ? (
                <>
                  <div className="mt-1 flex items-baseline gap-2">
                    <StatusBadge tone={tone(d.technical.decision)}>{d.technical.decision}</StatusBadge>
                    <span className="num text-[11px] text-dim">${fmtNum(d.technical.last_close ?? 0, 2)}</span>
                  </div>
                  <div className="mt-1 text-[10px] text-faint">
                    MR {d.technical.mean_reversion} · TF {d.technical.trend_following}
                    {d.technical.atr_14 != null && ` · ATR ${d.technical.atr_14.toFixed(2)}`}
                  </div>
                  {d.technical.freshness_note && <div className="text-[10px] text-warn">{d.technical.freshness_note}</div>}
                </>
              ) : <div className="mt-1 text-[11px] text-faint">insufficient bars</div>}
            </div>
            <div className="glass-tile p-3">
              <div className="text-[10px] uppercase tracking-wider text-dim">Valuation</div>
              {d?.valuation && (d.valuation.sticker || d.valuation.fair) ? (
                <div className="num mt-1 text-[12px] space-y-0.5">
                  {d.valuation.sticker && <div>sticker <b>${fmtNum(d.valuation.sticker, 2)}</b></div>}
                  {d.valuation.buy && <div>buy below <b className="text-pos">${fmtNum(d.valuation.buy, 2)}</b></div>}
                  {d.valuation.fair && <div>fair <b>${fmtNum(d.valuation.fair, 2)}</b></div>}
                </div>
              ) : <div className="mt-1 text-[11px] text-faint">{d?.valuation ? "valuation ran — empty outputs" : "no valuation yet"}</div>}
            </div>
            <div className="glass-tile p-3">
              <div className="text-[10px] uppercase tracking-wider text-dim">Committee</div>
              {d?.decision ? (
                <>
                  <StatusBadge tone={tone(d.decision.verdict)}>{d.decision.verdict}</StatusBadge>
                  <div className="mt-1 text-[10px] text-faint">{fmtTime(d.decision.at)}</div>
                </>
              ) : <div className="mt-1 text-[11px] text-faint">no decision yet</div>}
            </div>
          </section>

          {d?.dossier && (
            <section className="glass-tile p-3 flex items-center justify-between">
              <div>
                <span className="text-[12px] font-medium">Research dossier v{d.dossier.version}</span>
                <span className="ml-2 text-[11px] text-faint">{d.dossier.sections} sections</span>
              </div>
              <a href={`/research?symbol=${sym}`} className="rounded-full border border-border px-2.5 py-0.5 text-[11px] text-dim hover:text-text">
                open →
              </a>
            </section>
          )}

          {/* real news — Alpaca market news + SEC filings */}
          <section>
            <div className="mb-1.5 flex items-baseline justify-between">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-dim">Market news</span>
              {news?.sources?.length ? <span className="text-[10px] text-faint">{news.sources.join(" + ")}</span> : null}
            </div>
            {!news ? (
              <div className="text-[11px] text-faint">loading…</div>
            ) : news.items.length === 0 ? (
              <div className="rounded-lg border border-border/60 p-3 text-[11px] text-faint">
                No news — configure ALPACA_API_KEY in Settings for headlines; SEC filings appear when the instrument has a CIK.
              </div>
            ) : (
              <ul className="space-y-1.5">
                {news.items.map((n, i) => (
                  <li key={i}>
                    <a href={n.url ?? undefined} target="_blank" rel="noreferrer"
                      className="glass-tile block px-3 py-2 transition hover:border-accent/40">
                      <div className="flex items-center justify-between text-[10px] text-faint">
                        <span className="uppercase tracking-wider">{n.source}</span>
                        <span className="num">{n.at ? fmtTime(n.at) : ""}</span>
                      </div>
                      <div className="mt-0.5 text-[12px] font-medium text-text leading-snug">{n.title}</div>
                      {n.summary && <div className="mt-0.5 line-clamp-2 text-[11px] text-dim">{n.summary}</div>}
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </aside>
    </div>
  );
}
