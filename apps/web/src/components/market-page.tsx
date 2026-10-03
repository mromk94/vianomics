"use client";

import { useEffect, useState } from "react";

import { apiGet } from "@/lib/api";
import { fmtNum, fmtPct } from "@/lib/format";
import { DataTable } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { SkeletonRows } from "@/components/ui/skeleton";

interface Snap {
  symbol: string; name: string; asset_class: string;
  close: number; change_pct: number | null; high: number;
  low: number; volume: number; as_of: string;
}
interface Quote {
  symbol: string; name: string | null; source: string;
  bid: number | null; ask: number | null; mid: number | null;
  spread: number | null; prev_close: number | null;
  ts: string; age_s: number; stale: boolean;
}
interface Signal {
  symbol: string; name: string; asset_class: string;
  close: number; as_of: string;
  ret_1d: number | null; ret_1w: number | null; ret_1m: number | null;
  sma50: number | null; sma200: number | null;
  above_sma50: boolean | null; above_sma200: boolean | null;
  rsi14: number | null; adx14: number | null;
  macd: { macd?: number; signal?: number; hist?: number } | null;
  atr_pct: number | null;
  from_52w_high: number | null; from_52w_low: number | null;
  vol_ratio: number | null;
}

const tone = (v: number | null | undefined, invert = false) => {
  if (v == null) return "text-faint";
  const pos = invert ? v < 0 : v >= 0;
  return pos ? "text-pos" : "text-neg";
};
const chg = (v: number | null | undefined, invert = false) => (
  <span className={`num ${tone(v, invert)}`}>
    {v != null ? `${v >= 0 ? "+" : ""}${fmtPct(v * 100)}` : "—"}
  </span>);

export function MarketPage() {
  const [rows, setRows] = useState<Snap[] | null>(null);
  const [live, setLive] = useState<Quote[] | null>(null);
  const [sigs, setSigs] = useState<Signal[] | null>(null);
  const [view, setView] = useState<"snapshot" | "signals">("snapshot");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    apiGet<Snap[]>("/api/v1/market/snapshot").then(setRows)
      .catch((e) => setError(e.message));
    apiGet<Quote[]>("/api/v1/market/quotes").then(setLive).catch(() => setLive([]));
    apiGet<Signal[]>("/api/v1/market/signals").then(setSigs).catch(() => setSigs([]));
  }, []);

  return (
    <div className="space-y-4">
      <PageHeader title="Market Overview"
        info="Daily bars are delayed EOD. Quotes are live/near-live — MT4 rows update every minute while the EA runs; Yahoo rows refresh on backfill."
        subtitle="live tape + daily bars + full technical signal surface"
        meta={rows ? `${rows.length} instruments` : ""}
        actions={
          <div className="flex rounded-lg border border-border p-0.5 text-[12px]">
            {(["snapshot", "signals"] as const).map((v) => (
              <button key={v} onClick={() => setView(v)}
                className={`rounded-md px-3 py-1 capitalize ${view === v ? "bg-accent font-semibold text-[#0b0f1a]" : "text-dim"}`}>
                {v}
              </button>
            ))}
          </div>
        } />
      {error && <ErrorState title="API error" detail={error} />}

      {(live?.length ?? 0) > 0 && (
        <SectionCard title="Live Tape" className="rise"
          action="bid/ask — MT4 EA push · yahoo refresh">
          <DataTable
            columns={[
              { key: "s", header: "Symbol", render: (r: Quote) => <span className="font-semibold text-accent">{r.symbol}</span> },
              { key: "src", header: "Source", render: (r) => <span className="text-[10px] uppercase tracking-wider text-faint">{r.source}</span> },
              { key: "b", header: "Bid", align: "right", render: (r) => <span className="num">{r.bid != null ? fmtNum(r.bid, 4) : "—"}</span> },
              { key: "a", header: "Ask", align: "right", render: (r) => <span className="num">{r.ask != null ? fmtNum(r.ask, 4) : "—"}</span> },
              { key: "sp", header: "Spread", align: "right", render: (r) => <span className="num text-faint">{r.spread != null ? fmtNum(r.spread, 4) : "—"}</span> },
              { key: "pc", header: "Prev close", align: "right", render: (r) => <span className="num text-dim">{r.prev_close != null ? fmtNum(r.prev_close, 2) : "—"}</span> },
              { key: "t", header: "Age", align: "right", render: (r) => (
                <span className={`num text-[11px] ${r.stale ? "text-neg" : "text-faint"}`}>
                  {r.age_s < 120 ? `${r.age_s}s` : r.age_s < 7200 ? `${Math.round(r.age_s / 60)}m` : `${Math.round(r.age_s / 3600)}h`}
                  {r.stale && " · stale"}
                </span>) },
            ]}
            rows={live ?? []} rowKey={(r) => `${r.source}:${r.symbol}`}
          />
        </SectionCard>
      )}

      <SectionCard className="rise"
        title={view === "signals" ? "Signal Surface" : "Daily Snapshot"}
        action={view === "signals" ? "trend · momentum · vol · 52w" : "last daily bar"}>
        {view === "snapshot" ? (
          !rows ? <SkeletonRows /> : rows.length === 0 ? (
            <EmptyState title="No bars" hint="Market data ingests via /data-ops jobs." />
          ) : (
            <DataTable
              columns={[
                { key: "s", header: "Symbol", render: (r: Snap) => <span className="font-semibold text-accent">{r.symbol}</span> },
                { key: "n", header: "Name", render: (r) => <span className="text-dim">{r.name}</span> },
                { key: "c", header: "Close", align: "right", render: (r) => <span className="num">${fmtNum(r.close, 2)}</span> },
                { key: "ch", header: "Day Δ", align: "right", render: (r) => <span className={`num ${(r.change_pct ?? 0) >= 0 ? "text-pos" : "text-neg"}`}>{r.change_pct != null ? fmtPct(r.change_pct) : "—"}</span> },
                { key: "h", header: "High", align: "right", render: (r) => <span className="num text-faint">{fmtNum(r.high, 2)}</span> },
                { key: "l", header: "Low", align: "right", render: (r) => <span className="num text-faint">{fmtNum(r.low, 2)}</span> },
                { key: "v", header: "Vol", align: "right", render: (r) => <span className="num text-faint">{fmtNum(r.volume / 1e6, 1)}M</span> },
                { key: "t", header: "As of", align: "right", render: (r) => <span className="num text-faint text-[11px]">{r.as_of}</span> },
              ]}
              rows={rows} rowKey={(r) => r.symbol}
            />
          )
        ) : (
          !sigs ? <SkeletonRows /> : sigs.length === 0 ? (
            <EmptyState title="No signals" hint="Signals need ≥30 daily bars per instrument — run /data-ops backfill." />
          ) : (
            <DataTable
              columns={[
                { key: "s", header: "Symbol", render: (r: Signal) => <span className="font-semibold text-accent">{r.symbol}</span> },
                { key: "c", header: "Close", align: "right", render: (r) => <span className="num">${fmtNum(r.close, 2)}</span> },
                { key: "d", header: "1D", align: "right", render: (r) => chg(r.ret_1d) },
                { key: "w", header: "1W", align: "right", render: (r) => chg(r.ret_1w) },
                { key: "m", header: "1M", align: "right", render: (r) => chg(r.ret_1m) },
                { key: "rsi", header: "RSI-14", align: "right", render: (r) => (
                  <span className={`num ${(r.rsi14 ?? 50) >= 70 ? "text-neg" : (r.rsi14 ?? 50) <= 30 ? "text-pos" : "text-dim"}`}>
                    {r.rsi14 != null ? fmtNum(r.rsi14, 0) : "—"}</span>) },
                { key: "adx", header: "ADX", align: "right", render: (r) => (
                  <span className={`num ${(r.adx14 ?? 0) >= 25 ? "text-accent" : "text-dim"}`}>
                    {r.adx14 != null ? fmtNum(r.adx14, 0) : "—"}</span>) },
                { key: "tr", header: "Trend", align: "right", render: (r) => (
                  <span className={`text-[11px] font-semibold ${
                    r.above_sma200 && r.above_sma50 ? "text-pos" :
                    !r.above_sma200 && !r.above_sma50 ? "text-neg" : "text-warn"}`}>
                    {r.above_sma200 == null ? "—"
                      : r.above_sma200 && r.above_sma50 ? "▲▲"
                      : r.above_sma200 ? "▲" : r.above_sma50 ? "▽" : "▼▼"}
                  </span>) },
                { key: "atr", header: "ATR%", align: "right", render: (r) => <span className="num text-dim">{r.atr_pct != null ? fmtPct(r.atr_pct * 100) : "—"}</span> },
                { key: "h52", header: "vs 52w hi", align: "right", render: (r) => chg(r.from_52w_high) },
                { key: "vol", header: "Vol×", align: "right", render: (r) => (
                  <span className={`num ${(r.vol_ratio ?? 1) > 1.5 ? "text-accent" : "text-dim"}`}>
                    {r.vol_ratio != null ? `${fmtNum(r.vol_ratio, 1)}×` : "—"}</span>) },
                { key: "t", header: "As of", align: "right", render: (r) => <span className="num text-faint text-[11px]">{r.as_of}</span> },
              ]}
              rows={sigs} rowKey={(r) => r.symbol}
            />
          )
        )}
      </SectionCard>
    </div>
  );
}
