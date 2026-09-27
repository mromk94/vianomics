"use client";

import { useEffect, useRef, useState } from "react";
import { CandlestickSeries, createChart, HistogramSeries, LineSeries } from "lightweight-charts";

import { apiGet } from "@/lib/api";
import { fmtNum, fmtTime } from "@/lib/format";
import { DataTable, type Column } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { SearchInput } from "@/components/ui/search-input";
import { SectionCard } from "@/components/ui/section-card";
import { StatusBadge } from "@/components/ui/status-badge";

interface Bar {
  time: string; open: number; high: number; low: number;
  close: number; volume: number; provisional: boolean;
}

interface Branch {
  decision: string;
  explanation?: string;
  indicators?: Record<string, any>;
  confirmations?: Record<string, boolean>;
  trigger?: { rsi_10_1d: number | null; threshold: number; fired: boolean };
  sequence?: string[];
  first_failed_gate?: string | null;
  levels?: { resistance: number; support: number } | null;
}

interface Signal {
  symbol: string; bars: number; last_close: number | null;
  last_bar_time: string | null; data_fresh: boolean;
  data_source: string; params_version: string;
  decision: string; explanation: string; freshness_note?: string;
  mean_reversion: Branch; trend_following: Branch;
}

interface ScanRow {
  symbol: string; decision: string; mr: string; tf: string;
  rsi_1d: number | null; aroon_up: number | null;
  last_close: number | null; fresh: boolean;
}

interface ExecOrder {
  id: string; symbol: string; broker: string; side: string;
  qty: number; status: string; filled_qty: number;
  avg_fill: number | null; commission: number | null;
  reject: string | null; submit_latency_ms: number | null;
  idempotency: string; created_at: string;
}

interface ExecStatus {
  execution_enabled: boolean; execution_broker: string;
  kill_switch: boolean; fsm_version: string;
  adapters: Record<string, { operational: boolean; reason?: string; mode?: string }>;
}

const TFS = ["1d", "2d", "3d", "1w", "1mo"] as const;
const TONE: Record<string, "pos" | "warn" | "neg" | "info"> = {
  entry_signal: "pos", wait: "warn", invalid_data: "info",
};

function sma(vals: number[], n: number): (number | null)[] {
  return vals.map((_, i) => i < n - 1 ? null
    : vals.slice(i - n + 1, i + 1).reduce((a, b) => a + b) / n);
}

export function TradingPage() {
  const [symbol, setSymbol] = useState("NVDA");
  const [tf, setTf] = useState<string>("1d");
  const [branch, setBranch] = useState<"mr" | "tf">("mr");
  const [bars, setBars] = useState<Bar[]>([]);
  const [sig, setSig] = useState<Signal | null>(null);
  const [scan, setScan] = useState<ScanRow[]>([]);
  const [delayed, setDelayed] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [orders, setOrders] = useState<ExecOrder[]>([]);
  const [exec, setExec] = useState<ExecStatus | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    apiGet<ScanRow[]>("/api/v1/technical/scan").then(setScan).catch(() => {});
    apiGet<ExecOrder[]>("/api/v1/execution/orders").then(setOrders).catch(() => {});
    apiGet<ExecStatus>("/api/v1/execution/status").then(setExec).catch(() => {});
  }, []);

  useEffect(() => {
    setError(null);
    Promise.all([
      apiGet<{ bars: Bar[]; delayed: boolean }>(`/api/v1/technical/bars/${symbol}?timeframe=${tf}&limit=400`),
      apiGet<Signal>(`/api/v1/technical/signal/${symbol}`),
    ]).then(([b, s]) => { setBars(b.bars); setDelayed(b.delayed); setSig(s); })
      .catch((e) => setError(e.message));
  }, [symbol, tf]);

  // chart
  useEffect(() => {
    if (!chartRef.current || bars.length === 0) return;
    const chart = createChart(chartRef.current, {
      height: 420,
      layout: { background: { color: "transparent" }, textColor: "#64748b", fontSize: 11 },
      grid: { vertLines: { color: "#1e293b33" }, horzLines: { color: "#1e293b33" } },
      rightPriceScale: { borderColor: "#1e293b" },
      timeScale: { borderColor: "#1e293b", timeVisible: false },
    });
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: "#34d399", downColor: "#f87171",
      wickUpColor: "#34d399", wickDownColor: "#f87171",
      borderVisible: false,
    });
    candle.setData(bars.map((b) => ({
      time: b.time, open: b.open, high: b.high, low: b.low, close: b.close,
    })) as any);
    // SMA overlays
    const closes = bars.map((b) => b.close);
    for (const [n, color] of [[20, "#fbbf24"], [50, "#60a5fa"]] as const) {
      const s = chart.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      s.setData(bars.map((b, i) => ({ time: b.time, value: sma(closes, n)[i] })).filter((p) => p.value != null) as any);
    }
    // volume
    const vol = chart.addSeries(HistogramSeries, {
      priceScaleId: "vol", priceFormat: { type: "volume" },
    });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(bars.map((b) => ({
      time: b.time, value: b.volume,
      color: b.close >= b.open ? "#34d39933" : "#f8717133",
    })) as any);
    // S/R lines from signal
    const lv = sig?.mean_reversion?.levels;
    if (lv) {
      candle.createPriceLine({ price: lv.resistance, color: "#f87171", lineStyle: 2, lineWidth: 1, title: "R" });
      candle.createPriceLine({ price: lv.support, color: "#34d399", lineStyle: 2, lineWidth: 1, title: "S" });
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars, sig]);

  const b = branch === "mr" ? sig?.mean_reversion : sig?.trend_following;

  const scanCols: Column<ScanRow>[] = [
    { key: "s", header: "Ticker", render: (r) => <button className="font-semibold text-accent hover:underline" onClick={() => setSymbol(r.symbol)}>{r.symbol}</button> },
    { key: "d", header: "Signal", render: (r) => <StatusBadge tone={TONE[r.decision]}>{r.decision.replace(/_/g, " ")}</StatusBadge> },
    { key: "mr", header: "MR", render: (r) => <StatusBadge tone={TONE[r.mr]}>{r.mr.replace(/_/g, " ")}</StatusBadge> },
    { key: "tf", header: "TF", render: (r) => <StatusBadge tone={TONE[r.tf]}>{r.tf.replace(/_/g, " ")}</StatusBadge> },
    { key: "rsi", header: "RSI(10)", align: "right", render: (r) => <span className={`num ${(r.rsi_1d ?? 100) < 30 ? "text-pos" : ""}`}>{r.rsi_1d?.toFixed(0) ?? "—"}</span> },
    { key: "ar", header: "Aroon↑", align: "right", render: (r) => <span className={`num ${(r.aroon_up ?? 0) > 99 ? "text-pos" : ""}`}>{r.aroon_up?.toFixed(0) ?? "—"}</span> },
    { key: "px", header: "Close", align: "right", render: (r) => <span className="num">{r.last_close ? `$${fmtNum(r.last_close, 2)}` : "—"}</span> },
    { key: "fr", header: "Fresh", render: (r) => r.fresh ? <span className="text-pos">●</span> : <StatusBadge tone="warn">stale</StatusBadge> },
  ];

  // trade setup preview (inspection only)
  const atrProxy = sig && sig.last_close ? sig.last_close * 0.03 : null;

  return (
    <div className="space-y-4">
      <PageHeader title="Trading Desk" info='Chart + order blotter. Orders come only from approved decisions; kill switch and size caps apply. Live trading stays off unless explicitly configured.' subtitle={`Branch A mean reversion · Branch B trend following — ${sig?.params_version ?? ""}`}
        meta={sig ? `${sig.bars} bars · last ${sig.last_bar_time?.slice(0, 10)} · ${delayed ? "delayed EOD (yahoo)" : ""}` : undefined}
        actions={<SearchInput value={symbol} onChange={(v) => setSymbol(v.toUpperCase())} className="w-32" />} />
      {sig?.freshness_note && <div className="glass border-warn/40 p-3 text-[12px] text-warn">{sig.freshness_note}</div>}
      {error && <ErrorState title="API error" detail={error} />}

      {/* chart */}
      <SectionCard title={symbol} className="rise"
        action={
          <div className="flex gap-1">
            {TFS.map((t) => (
              <button key={t} onClick={() => setTf(t)}
                className={`rounded-full px-2.5 py-1 text-[11px] transition ${tf === t ? "bg-accent text-[#0b0f1a] font-semibold" : "border border-border text-dim hover:text-text"}`}>{t}</button>
            ))}
          </div>
        }>
        <div ref={chartRef} className="w-full" />
        {bars.some((x) => x.provisional) && (
          <div className="mt-1 text-[10px] text-faint">last aggregated bar provisional (incomplete period) — excluded from signal math</div>
        )}
      </SectionCard>

      {/* branch panels */}
      <div className="rise rise-1 grid gap-3 lg:grid-cols-2">
        <SectionCard title={`${symbol} — ${branch === "mr" ? "Mean Reversion" : "Trend Following"}`}
          action={
            <div className="flex gap-1">
              {(["mr", "tf"] as const).map((x) => (
                <button key={x} onClick={() => setBranch(x)}
                  className={`rounded-full px-3 py-1 text-[11px] capitalize transition ${branch === x ? "bg-accent text-[#0b0f1a] font-semibold" : "border border-border text-dim"}`}>
                  {x === "mr" ? "Mean Rev" : "Trend"}
                </button>
              ))}
            </div>
          }>
          {b ? (
            <>
              <div className="mb-3 flex items-center gap-2">
                <StatusBadge tone={TONE[b.decision]} dot>{b.decision.replace(/_/g, " ")}</StatusBadge>
                <span className="text-[12px] text-dim">{b.explanation}</span>
              </div>
              {branch === "mr" ? (
                <div className="grid grid-cols-2 gap-2 text-[12px] sm:grid-cols-4">
                  {[
                    ["RSI(10) 1d", b.indicators?.rsi_10_1d],
                    ["RSI(10) 1w", b.indicators?.rsi_10_1w],
                    ["CMI(21)", b.indicators?.cmi_21],
                    ["%R(13)", b.indicators?.wr_13],
                  ].map(([l, v]) => (
                    <div key={l as string} className="glass-tile px-2.5 py-1.5">
                      <div className="text-[10px] text-dim">{l}</div>
                      <div className="num font-semibold">{(v as number) != null ? fmtNum(v as number, 1) : "—"}</div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="space-y-1">
                  {(b.sequence ?? []).map((gate) => (
                    <div key={gate} className="flex items-center justify-between border-b border-border/40 py-1 text-[12px] last:border-0">
                      <span className="capitalize text-dim">{gate}</span>
                      <StatusBadge tone={b.confirmations?.[gate] ? "pos" : gate === b.first_failed_gate ? "neg" : "warn"}>
                        {b.confirmations?.[gate] ? "pass" : gate === b.first_failed_gate ? "halted" : "not reached"}
                      </StatusBadge>
                    </div>
                  ))}
                </div>
              )}
              {b.levels && (
                <div className="mt-3 flex gap-4 text-[12px]">
                  <span className="text-dim">S <span className="num text-pos">${fmtNum(b.levels.support, 2)}</span></span>
                  <span className="text-dim">R <span className="num text-neg">${fmtNum(b.levels.resistance, 2)}</span></span>
                </div>
              )}
            </>
          ) : <EmptyState title="No signal" />}
        </SectionCard>

        {/* trade setup preview */}
        <SectionCard title="Trade setup preview" action="inspection only — no order routing">
          {sig?.last_close && atrProxy ? (() => {
            const entry = sig.last_close;
            const stop = entry - atrProxy;
            const t1 = entry + 3 * atrProxy;
            const rr = ((t1 - entry) / (entry - stop)).toFixed(1);
            return (
              <div className="space-y-2 text-[13px]">
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {[
                    ["Entry (last)", `$${fmtNum(entry, 2)}`],
                    ["Stop (−1×ATR≈3%)", `$${fmtNum(stop, 2)}`],
                    ["Target (+3×ATR)", `$${fmtNum(t1, 2)}`],
                    ["R : R", `${rr} : 1`],
                  ].map(([l, v]) => (
                    <div key={l} className="glass-tile px-3 py-2">
                      <div className="text-[10px] text-dim uppercase">{l}</div>
                      <div className="num mt-0.5 font-semibold">{v}</div>
                    </div>
                  ))}
                </div>
                <p className="text-[11px] text-faint">
                  Sizing follows Part 12 pyramid rules ($Risk → 1.5×ATR); this card is a preview only — no order is staged or routed.
                  ATR proxy = 3% of price until Part 12 lands.
                </p>
              </div>
            );
          })() : <EmptyState title="No price data" />}
        </SectionCard>
      </div>

      {/* scan table */}
      <SectionCard title="Universe scan" className="rise rise-2">
        <DataTable columns={scanCols} rows={scan} rowKey={(r) => r.symbol} />
      </SectionCard>

      {/* Part 30 — execution blotter */}
      <SectionCard title={`Order blotter — ${exec?.execution_broker ?? "paper"} lane`} className="rise"
        action={
          <div className="flex items-center gap-2">
            {exec?.kill_switch && <StatusBadge tone="neg">KILL SWITCH ON</StatusBadge>}
            {!exec?.execution_enabled && <StatusBadge tone="info">live disabled</StatusBadge>}
            <span className="text-[10px] text-faint">{exec?.fsm_version}</span>
          </div>
        }>
        {orders.length === 0 ? (
          <EmptyState title="No orders" hint="Approved order tickets submit through the paper lane; live trading stays disabled until configured." />
        ) : (
          <DataTable
            columns={[
              { key: "s", header: "Symbol", render: (r: ExecOrder) => <span className="font-semibold text-accent">{r.symbol}</span> },
              { key: "sd", header: "Side", render: (r) => <StatusBadge tone={r.side === "buy" ? "pos" : "neg"}>{r.side}</StatusBadge> },
              { key: "q", header: "Qty", align: "right", render: (r) => <span className="num">{r.qty}</span> },
              { key: "st", header: "Status", render: (r) => <StatusBadge tone={r.status === "filled" ? "pos" : r.status === "rejected" ? "neg" : "info"}>{r.status}</StatusBadge> },
              { key: "f", header: "Fill", align: "right", render: (r) => <span className="num">{r.avg_fill ? `$${fmtNum(r.avg_fill, 2)}` : "—"}</span> },
              { key: "c", header: "Comm.", align: "right", render: (r) => <span className="num">{r.commission != null ? `$${r.commission}` : "—"}</span> },
              { key: "l", header: "Latency", align: "right", render: (r) => <span className="num text-faint">{r.submit_latency_ms ?? "—"}ms</span> },
              { key: "id", header: "Idem", render: (r) => <span className="text-[10px] text-faint">{r.idempotency}</span> },
              { key: "t", header: "Time", align: "right", render: (r) => <span className="num text-faint text-[11px]">{fmtTime(r.created_at)}</span> },
            ]}
            rows={orders} rowKey={(r) => r.id}
          />
        )}
        {exec && (
          <div className="mt-2 flex gap-4 text-[10px] text-faint">
            {Object.entries(exec.adapters).map(([k, a]) => (
              <span key={k}>{k}: {a.operational ? <span className="text-pos">{a.mode}</span> : <span title={a.reason}>not configured</span>}</span>
            ))}
          </div>
        )}
      </SectionCard>
    </div>
  );
}
