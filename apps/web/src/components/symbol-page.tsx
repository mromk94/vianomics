"use client";

import { useEffect, useRef, useState } from "react";
import { CandlestickSeries, createChart } from "lightweight-charts";

import { apiGet } from "@/lib/api";
import { fmtNum, fmtTime } from "@/lib/format";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { StatusBadge } from "@/components/ui/status-badge";
import { PyramidModal } from "@/components/pyramid-modal";

interface NewsItem { source: string; title: string; url: string | null;
  at: string | null; summary: string | null }

const TFS = [
  { v: "1d", label: "Daily" }, { v: "2d", label: "2-Day" },
  { v: "3d", label: "3-Day" }, { v: "1w", label: "Weekly" },
  { v: "1mo", label: "Monthly" }, { v: "1y", label: "Yearly" },
];

const tone = (v?: string | null) =>
  v === "entry_signal" || v === "pass" || v === "approve_pending_human"
    ? "pos" : v === "fail" || v === "no_trade" || v === "reject"
      ? "neg" : "info" as const;

function Pct({ v }: { v: number | null | undefined }) {
  if (v == null) return <span className="text-faint">—</span>;
  return <span className={v >= 0 ? "text-pos" : "text-neg"}>
    {v >= 0 ? "+" : ""}{(v * 100).toFixed(2)}%</span>;
}

function SigCell({ l, v }: { l: string; v: React.ReactNode }) {
  return (
    <div className="glass-tile px-3 py-2">
      <div className="text-[9px] uppercase tracking-wide text-faint">{l}</div>
      <div className="num mt-0.5 text-[13px] font-semibold">{v}</div>
    </div>
  );
}

export function SymbolPage({ symbol }: { symbol: string }) {
  const sym = symbol.toUpperCase();
  const [d, setD] = useState<any | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [news, setNews] = useState<{ items: NewsItem[]; sources: string[] } | null>(null);
  const [tf, setTf] = useState("1d");
  const [bars, setBars] = useState<any[]>([]);
  const [chartMode, setChartMode] = useState<"tv" | "history">("tv");
  const [pyrOpen, setPyrOpen] = useState(false);
  const chartRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setD(null); setErr(null);
    apiGet<any>(`/api/v1/symbol/${sym}`).then(setD)
      .catch((e) => setErr(e.message));
    setNews(null);
    apiGet<{ items: NewsItem[]; sources: string[] }>(`/api/v1/news/${sym}`).then(setNews)
      .catch(() => setNews({ items: [], sources: [] }));
  }, [sym]);

  useEffect(() => {
    apiGet<{ bars: any[] }>(
      `/api/v1/technical/bars/${sym}?timeframe=${tf}&limit=1500`)
      .then((r) => setBars(r.bars)).catch(() => setBars([]));
  }, [sym, tf]);

  // price-history chart
  useEffect(() => {
    if (chartMode !== "history" || !chartRef.current || !bars.length) return;
    const chart = createChart(chartRef.current, {
      autoSize: true, height: 320,
      layout: { background: { color: "transparent" },
        textColor: "#64748b", fontSize: 11 },
      grid: { vertLines: { color: "#1e293b33" },
        horzLines: { color: "#1e293b33" } },
      rightPriceScale: { borderColor: "#1e293b" },
      timeScale: { borderColor: "#1e293b" },
    });
    chart.addSeries(CandlestickSeries, {
      upColor: "#34d399", downColor: "#f87171",
      wickUpColor: "#34d399", wickDownColor: "#f87171",
      borderVisible: false,
    }).setData(bars.map((b) => ({
      time: b.time, open: b.open, high: b.high,
      low: b.low, close: b.close })) as any);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars, chartMode]);

  if (err) return (
    <div className="p-6"><ErrorState title={sym} detail={err} /></div>);

  const s = d?.signals;
  const pos = d?.position;
  const atr = s?.atr_pct;

  return (
    <div className="space-y-4 p-4 sm:p-6">
      {/* header — market overview strip */}
      <PageHeader
        title={`${sym}${d?.name ? ` — ${d.name}` : ""}`}
        meta={[d?.sector, d?.exchange, d?.asset_class,
               s ? `as of ${s.as_of}` : null]
              .filter(Boolean).join(" · ") || undefined}
        actions={
          <button onClick={() => setPyrOpen(true)}
            className="rounded-full bg-accent px-4 py-1.5 text-[12px] font-semibold text-[#0b0f1a] hover:brightness-110">
            Pyramid calculator
          </button>} />

      {s && (
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
          <SigCell l="Last" v={`$${fmtNum(s.close, 2)}`} />
          <SigCell l="1D" v={<Pct v={s.ret_1d} />} />
          <SigCell l="1W" v={<Pct v={s.ret_1w} />} />
          <SigCell l="1M" v={<Pct v={s.ret_1m} />} />
          <SigCell l="ATR14" v={s.atr_pct ? `${(s.atr_pct * 100).toFixed(2)}%` : "—"} />
          <SigCell l="Vol ×20d" v={s.vol_ratio ? `${fmtNum(s.vol_ratio, 2)}×` : "—"} />
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-3">
        {/* charts — TradingView / stored price history */}
        <SectionCard className="xl:col-span-2"
          title={chartMode === "tv" ? "Chart — TradingView" : "Price history — stored bars"}
          action={
            <div className="flex items-center gap-2">
              {chartMode === "history" && (
                <select value={tf} onChange={(e) => setTf(e.target.value)}
                  className="rounded border border-border bg-surface-2 px-2 py-0.5 text-[11px] text-dim focus:border-accent focus:outline-none">
                  {TFS.map((t) => <option key={t.v} value={t.v}>{t.label}</option>)}
                </select>
              )}
              <button onClick={() => setChartMode(chartMode === "tv" ? "history" : "tv")}
                className="rounded-full border border-border px-3 py-0.5 text-[11px] text-dim hover:text-text">
                {chartMode === "tv" ? "Price history" : "TradingView"}
              </button>
            </div>}>
          {chartMode === "tv" ? (
            <iframe key={sym} title={`${sym} TradingView`}
              src={`https://www.tradingview.com/widgetembed/?symbol=${encodeURIComponent(sym)}&interval=D&theme=dark&style=1&timezone=Etc%2FUTC&withdateranges=1&hide_side_toolbar=0&allow_symbol_change=0&studies=%5B%22RSI%40tv-basicstudies%22%2C%22ATR%40tv-basicstudies%22%5D`}
              className="h-[380px] w-full border-0" allow="fullscreen" />
          ) : bars.length ? (
            <div ref={chartRef} className="h-[380px] w-full" />
          ) : (
            <EmptyState title="No stored bars"
              hint="Daily bars ingest via the dataops backfill." />
          )}
        </SectionCard>

        {/* right rail — position + engine verdicts */}
        <div className="space-y-4">
          {pos && (
            <SectionCard title={`Held position · ${pos.source ?? "book"}`}>
              <div className="num grid grid-cols-2 gap-2 text-[12px] sm:grid-cols-4 xl:grid-cols-2">
                <div><div className="text-[9px] text-faint">QTY</div>{fmtNum(pos.quantity, 0)}</div>
                <div><div className="text-[9px] text-faint">AVG</div>${fmtNum(pos.avg_cost, 2)}</div>
                <div><div className="text-[9px] text-faint">VALUE</div>${fmtNum(pos.market_value)}</div>
                <div><div className="text-[9px] text-faint">UNREAL.</div>
                  <span className={(pos.unrealized ?? 0) >= 0 ? "text-pos" : "text-neg"}>
                    {pos.unrealized != null ? `${pos.unrealized >= 0 ? "+" : ""}$${fmtNum(pos.unrealized)}` : "—"}
                  </span></div>
              </div>
            </SectionCard>
          )}

          <SectionCard title="Engine verdicts">
            <div className="grid grid-cols-2 gap-2">
              <div className="glass-tile p-2.5">
                <div className="text-[9px] uppercase text-dim">Green Zone</div>
                {d?.screening
                  ? <div className="mt-1 flex items-baseline gap-1.5">
                      <span className="num font-bold">{d.screening.score}<span className="text-[10px] text-faint">/20</span></span>
                      <StatusBadge tone={tone(d.screening.verdict)}>{d.screening.verdict}</StatusBadge>
                    </div>
                  : <div className="mt-1 text-[10px] text-faint">no screen</div>}
              </div>
              <div className="glass-tile p-2.5">
                <div className="text-[9px] uppercase text-dim">Technical</div>
                {d?.technical
                  ? <StatusBadge tone={tone(d.technical.decision)}>{d.technical.decision}</StatusBadge>
                  : <div className="mt-1 text-[10px] text-faint">no data</div>}
              </div>
              <div className="glass-tile p-2.5">
                <div className="text-[9px] uppercase text-dim">Valuation</div>
                {d?.valuation && (d.valuation.sticker || d.valuation.fair)
                  ? <div className="num mt-1 text-[11px]">
                      {d.valuation.sticker && <div>sticker ${fmtNum(d.valuation.sticker, 2)}</div>}
                      {d.valuation.buy && <div className="text-pos">buy ≤ ${fmtNum(d.valuation.buy, 2)}</div>}
                    </div>
                  : <div className="mt-1 text-[10px] text-faint">no valuation</div>}
              </div>
              <div className="glass-tile p-2.5">
                <div className="text-[9px] uppercase text-dim">Committee</div>
                {d?.decision
                  ? <StatusBadge tone={tone(d.decision.verdict)}>{d.decision.verdict}</StatusBadge>
                  : <div className="mt-1 text-[10px] text-faint">no decision</div>}
              </div>
            </div>
            {d?.dossier && (
              <a href={`/research?symbol=${sym}`}
                className="mt-2 block rounded-lg border border-border px-3 py-1.5 text-center text-[11px] text-dim hover:border-accent/40 hover:text-text">
                Research dossier v{d.dossier.version} ({d.dossier.sections} sections) →
              </a>
            )}
          </SectionCard>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        {/* market intelligence — full signal surface */}
        <SectionCard title="Market intelligence" className="xl:col-span-1"
          action={s ? `as of ${s.as_of}` : undefined}>
          {s ? (
            <div className="grid grid-cols-2 gap-2">
              <SigCell l="Trend" v={s.above_sma200 ? "above SMA200" : "below SMA200"} />
              <SigCell l="SMA50" v={s.sma50 ? `$${fmtNum(s.sma50, 2)}` : "—"} />
              <SigCell l="RSI-14" v={s.rsi14 ? fmtNum(s.rsi14, 1) : "—"} />
              <SigCell l="ADX-14" v={s.adx14 ? fmtNum(s.adx14, 1) : "—"} />
              <SigCell l="MACD" v={s.macd != null ? fmtNum(s.macd, 3) : "—"} />
              <SigCell l="52w high" v={<Pct v={s.from_52w_high} />} />
              <SigCell l="52w low" v={<Pct v={s.from_52w_low} />} />
              <SigCell l="Vol ratio" v={s.vol_ratio ? `${fmtNum(s.vol_ratio, 2)}×` : "—"} />
            </div>
          ) : <EmptyState title="No signal data" hint="needs ≥30 daily bars" />}
        </SectionCard>

        {/* risk & pyramid */}
        <SectionCard title="Risk & pyramid" className="xl:col-span-1"
          action={
            <button onClick={() => setPyrOpen(true)}
              className="rounded-full border border-accent/50 px-3 py-0.5 text-[11px] text-accent hover:bg-accent/10">
              Calculator
            </button>}>
          {s?.atr_pct ? (
            <div className="space-y-1.5 text-[12px]">
              <div className="num grid grid-cols-3 gap-2">
                <SigCell l="ATR14" v={`${(s.atr_pct * 100).toFixed(2)}%`} />
                <SigCell l="Stop −1.5×" v={s.close ? `$${fmtNum(s.close * (1 - 1.5 * s.atr_pct), 2)}` : "—"} />
                <SigCell l="Tgt +3×" v={s.close ? `$${fmtNum(s.close * (1 + 3 * s.atr_pct), 2)}` : "—"} />
              </div>
              {d?.pyramids?.length ? (
                <div className="space-y-1 pt-1">
                  {d.pyramids.map((p: any) => (
                    <div key={p.id}
                      className="flex items-center justify-between rounded-lg border border-border px-2.5 py-1.5 text-[11px]">
                      <StatusBadge tone={p.state === "trade_eligible" ? "warn" : "pos"}>{p.state}</StatusBadge>
                      <span className="num">entry ${fmtNum(p.entry, 2)}</span>
                      <span className="num text-neg">stop ${fmtNum(p.stop, 2)}</span>
                      <span className="num text-pos">tgt ${fmtNum(p.target1, 2)}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="pt-1 text-[11px] text-faint">
                  no open pyramid — size one in the calculator
                </div>
              )}
            </div>
          ) : <EmptyState title="No ATR" hint="needs daily bars" />}
        </SectionCard>

        {/* financials — latest SEC facts */}
        <SectionCard title="Financials" className="xl:col-span-1"
          action="latest reported facts">
          {d?.fundamentals?.length ? (
            <div className="max-h-56 space-y-1 overflow-y-auto text-[12px]">
              {d.fundamentals.map((f: any) => (
                <div key={f.concept}
                  className="flex items-center justify-between border-b border-border/40 py-1 last:border-0">
                  <span className="truncate pr-2 text-dim">
                    {f.concept.replace("us-gaap:", "").replace(/([a-z])([A-Z])/g, "$1 $2")}
                    <span className="ml-1 text-[9px] text-faint">{f.period_end}</span>
                  </span>
                  <span className="num shrink-0 font-medium">
                    {f.value == null ? "—" : f.unit === "USD" || f.unit === "shares"
                      ? `${fmtNum(f.value / 1e6, 1)}M`
                      : fmtNum(f.value, 2)}
                  </span>
                </div>
              ))}
            </div>
          ) : <EmptyState title="No fundamentals" hint="SEC facts ingest via ingest:edgar:facts" />}
        </SectionCard>
      </div>

      {/* news */}
      <SectionCard title="News"
        action={news?.sources?.length ? news.sources.join(" + ") : undefined}>
        {!news ? (
          <div className="text-[12px] text-faint">loading…</div>
        ) : news.items.length === 0 ? (
          <EmptyState title="No news"
            hint="configure ALPACA_API_KEY for headlines; SEC filings appear when the instrument has a CIK." />
        ) : (
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {news.items.map((n, i) => (
              <a key={i} href={n.url ?? undefined} target="_blank" rel="noreferrer"
                className="glass-tile block px-3 py-2 transition hover:border-accent/40">
                <div className="flex items-center justify-between text-[10px] text-faint">
                  <span className="uppercase tracking-wider">{n.source}</span>
                  <span className="num">{n.at ? fmtTime(n.at) : ""}</span>
                </div>
                <div className="mt-0.5 text-[12px] font-medium leading-snug">{n.title}</div>
                {n.summary && <div className="mt-0.5 line-clamp-2 text-[11px] text-dim">{n.summary}</div>}
              </a>
            ))}
          </div>
        )}
      </SectionCard>

      <PyramidModal open={pyrOpen} onClose={() => setPyrOpen(false)}
        initial={sym} onCreated={() => {
          apiGet(`/api/v1/symbol/${sym}`).then(setD).catch(() => {});
        }} />
    </div>
  );
}
