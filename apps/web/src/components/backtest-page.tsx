"use client";

import { useEffect, useRef, useState } from "react";
import { AreaSeries, createChart } from "lightweight-charts";

import { apiGet, apiPost, ApiError } from "@/lib/api";
import { fmtNum, fmtPct, fmtTime } from "@/lib/format";
import { DataTable } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { MetricCard } from "@/components/ui/metric-card";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { SkeletonRows } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";

interface RunRow {
  id: string; name: string; universe: string[]; engine: string;
  params: Record<string, unknown>;
  metrics: Record<string, number | null>; created_at: string;
  note: string;
}
interface RunDetail extends RunRow {
  equity_curve: { t: string; equity: number }[];
  trades: { symbol: string; entry: string; entry_px: number;
    exit: string | null; exit_px: number | null; qty: number;
    stop: number; pnl: number | null; reason: string | null }[];
  validation: { walk_forward?: { error?: string; windows: { window: number; from: string;
      to: string; cagr: number | null; sharpe: number | null;
      max_dd: number | null; trades: number | null }[];
    stability?: { mean_sharpe: number | null; all_windows_positive: boolean | null } };
    monte_carlo?: { median_final: number; p5: number; p95: number; prob_loss: number; note: string } };
  data_snapshot: Record<string, { bars: number; from: string; to: string }>;
}

function EquityCurve({ points }: { points: { t: string; equity: number }[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || !points.length) return;
    const chart = createChart(ref.current, {
      autoSize: true, height: 220,
      layout: { background: { color: "transparent" },
        textColor: "#5d6a8a", fontSize: 10 },
      grid: { vertLines: { visible: false }, horzLines: { color: "#1a2333" } },
      timeScale: { borderVisible: false },
      rightPriceScale: { borderVisible: false },
    });
    const s = chart.addSeries(AreaSeries, {
      lineColor: "#7c9cff", topColor: "rgba(124,156,255,0.35)",
      bottomColor: "rgba(124,156,255,0.02)", lineWidth: 2 });
    s.setData(points.map((p) => ({ time: p.t.slice(0, 10), value: p.equity })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [points]);
  return <div ref={ref} style={{ height: 220 }} />;
}

const lbl = "block text-[10px] uppercase tracking-wide text-faint mb-1";
const inp = "w-full rounded-lg border border-border bg-transparent px-3 py-1.5 text-[13px] num focus:border-accent focus:outline-none";

export function BacktestPage() {
  const [runs, setRuns] = useState<RunRow[] | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const chartRef = useRef<HTMLDivElement>(null);
  const [form, setForm] = useState({
    symbols: "NVDA,MSFT,GOOGL,AMD,PLTR",
    strategy: "mr_rsi", initial_capital: 100000,
    slippage_bps: 10, commission: 1,
    risk_pct: 2, entry_rsi: 30, max_positions: 5,
  });

  const load = () => apiGet<RunRow[]>("/api/v1/backtest/runs")
    .then(setRuns).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  const open = (id: string) =>
    apiGet<RunDetail>(`/api/v1/backtest/runs/${id}`).then(setDetail);

  const runBt = async () => {
    setBusy(true); setNotice(null); setError(null);
    try {
      const r = await apiPost<{ run_id: string }>("/api/v1/backtest/run", {
        symbols: form.symbols.split(",").map((s) => s.trim()).filter(Boolean),
        strategy: form.strategy,
        initial_capital: form.initial_capital,
        slippage_bps: form.slippage_bps,
        commission_per_trade: form.commission,
        risk_per_trade_pct: form.risk_pct / 100,
        entry_rsi: form.entry_rsi,
        max_positions: form.max_positions,
      });
      setNotice("Run complete — simulated, not live performance.");
      await load(); await open(r.run_id);
    } catch (e) {
      setNotice(e instanceof ApiError && e.status === 401
        ? "Sign in (Settings) to run backtests." : (e as Error).message);
    } finally { setBusy(false); }
  };

  return (
    <div className="space-y-4">
      <PageHeader title="Backtesting & Research Sandbox" info="Simulates a strategy over past data with realistic costs — signals decide at one day's close and fill at the next open, so results can't peek into the future. Results are simulated, never live performance."
        subtitle="event-driven, point-in-time — signals close(t) → fills open(t+1); simulated results are never live performance" />

      {notice && <div className="glass border-pos/40 p-3 text-[13px] text-pos">{notice}</div>}
      {error && <ErrorState title="API error" detail={error} onRetry={load} />}

      <div className="grid grid-cols-12 gap-4">
        {/* config */}
        <SectionCard title="Configure & run" className="rise col-span-4">
          <div className="space-y-3">
            <label><span className={lbl}>Universe (symbols)</span>
              <input className={inp} value={form.symbols}
                onChange={(e) => setForm({ ...form, symbols: e.target.value })} /></label>
            <label><span className={lbl}>Strategy</span>
              <select className={inp} value={form.strategy}
                onChange={(e) => setForm({ ...form, strategy: e.target.value })}>
                <option value="mr_rsi">Mean reversion — RSI(10) dip</option>
                <option value="tf_aroon">Trend — Aroon &gt;99 entry</option>
              </select></label>
            <div className="grid grid-cols-2 gap-2">
              <label><span className={lbl}>Capital $</span>
                <input className={inp} type="number" value={form.initial_capital}
                  onChange={(e) => setForm({ ...form, initial_capital: +e.target.value })} /></label>
              <label><span className={lbl}>Slippage bps</span>
                <input className={inp} type="number" value={form.slippage_bps}
                  onChange={(e) => setForm({ ...form, slippage_bps: +e.target.value })} /></label>
              <label><span className={lbl}>Commission $/trade</span>
                <input className={inp} type="number" value={form.commission}
                  onChange={(e) => setForm({ ...form, commission: +e.target.value })} /></label>
              <label><span className={lbl}>Risk %/trade</span>
                <input className={inp} type="number" value={form.risk_pct}
                  onChange={(e) => setForm({ ...form, risk_pct: +e.target.value })} /></label>
              <label><span className={lbl}>Entry RSI</span>
                <input className={inp} type="number" value={form.entry_rsi}
                  onChange={(e) => setForm({ ...form, entry_rsi: +e.target.value })} /></label>
              <label><span className={lbl}>Max positions</span>
                <input className={inp} type="number" value={form.max_positions}
                  onChange={(e) => setForm({ ...form, max_positions: +e.target.value })} /></label>
            </div>
            <button onClick={runBt} disabled={busy}
              className="w-full rounded-lg bg-accent py-2 text-[13px] font-semibold text-[#0b0f1a] hover:brightness-110 disabled:opacity-50">
              {busy ? "Running…" : "Run backtest"}
            </button>
            <p className="text-[10px] text-faint">
              Costs, slippage and liquidity caps are real inputs. Missing
              history is reported as a limitation, never fabricated.
            </p>
          </div>
        </SectionCard>

        {/* runs list */}
        <SectionCard title="Run history — versioned" className="rise col-span-8">
          {runs === null ? <SkeletonRows /> : runs.length === 0 ? (
            <EmptyState title="No runs" hint="Configure and run a backtest." />
          ) : (
            <DataTable
              columns={[
                { key: "n", header: "Name", render: (r: RunRow) => (
                  <button onClick={() => open(r.id)} className="text-left font-semibold text-accent hover:underline">{r.name}</button>) },
                { key: "u", header: "Universe", render: (r) => <span className="text-dim text-[11px]">{r.universe.slice(0, 4).join(",")}{r.universe.length > 4 ? ` +${r.universe.length - 4}` : ""}</span> },
                { key: "c", header: "CAGR", align: "right", render: (r) => <span className="num">{(r.metrics.cagr ?? 0) * 100 < 0 ? "" : ""}{fmtPct((r.metrics.cagr ?? 0) * 100)}</span> },
                { key: "s", header: "Sharpe", align: "right", render: (r) => <span className="num">{fmtNum(r.metrics.sharpe ?? 0, 2)}</span> },
                { key: "d", header: "MaxDD", align: "right", render: (r) => <span className="num text-neg">{fmtPct((r.metrics.max_drawdown ?? 0) * 100)}</span> },
                { key: "t", header: "Trades", align: "right", render: (r) => <span className="num">{r.metrics.trades}</span> },
                { key: "at", header: "Ran", align: "right", render: (r) => <span className="num text-faint text-[11px]">{fmtTime(r.created_at)}</span> },
              ]}
              rows={runs} rowKey={(r) => r.id}
            />
          )}
        </SectionCard>
      </div>

      {detail && (
        <>
          {/* metrics */}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-8">
            <MetricCard label="CAGR" value={fmtPct((detail.metrics.cagr ?? 0) * 100)} />
            <MetricCard label="Sharpe" value={fmtNum(detail.metrics.sharpe ?? 0, 2)} />
            <MetricCard label="Sortino" value={fmtNum(detail.metrics.sortino ?? 0, 2)} />
            <MetricCard label="Max DD" value={fmtPct((detail.metrics.max_drawdown ?? 0) * 100)} tone="neg" />
            <MetricCard label="Win rate" value={fmtPct((detail.metrics.win_rate ?? 0) * 100)} />
            <MetricCard label="Expectancy" value={`$${fmtNum(detail.metrics.expectancy ?? 0)}`} />
            <MetricCard label="Costs" value={`$${fmtNum(detail.metrics.total_costs ?? 0)}`} />
            <MetricCard label="Final" value={`$${fmtNum(detail.metrics.final_equity ?? 0)}`} />
          </div>

          {/* equity curve */}
          <SectionCard title={`Equity curve — ${detail.name}`} className="rise">
            <EquityCurve points={detail.equity_curve} />
          </SectionCard>

          <div className="grid grid-cols-12 gap-4">
            {/* trades */}
            <SectionCard title={`Trades (${detail.trades.length})`} className="rise col-span-7">
              {detail.trades.length === 0 ? (
                <EmptyState title="No trades" hint="Signals never fired on this window." />
              ) : (
                <div className="max-h-72 overflow-y-auto">
                  <DataTable
                    columns={[
                      { key: "s", header: "Sym", render: (t: RunDetail["trades"][0]) => <span className="font-semibold">{t.symbol}</span> },
                      { key: "e", header: "Entry", render: (t) => <span className="num text-[11px]">{t.entry.slice(0, 10)} @{fmtNum(t.entry_px)}</span> },
                      { key: "x", header: "Exit", render: (t) => <span className="num text-[11px]">{t.exit ? `${t.exit.slice(0, 10)} @${fmtNum(t.exit_px!)}` : "—"}</span> },
                      { key: "p", header: "P&L", align: "right", render: (t) => <span className={`num ${(t.pnl ?? 0) >= 0 ? "text-pos" : "text-neg"}`}>{t.pnl != null ? `$${fmtNum(t.pnl)}` : "—"}</span> },
                      { key: "r", header: "Reason", render: (t) => <StatusBadge tone="info">{t.reason}</StatusBadge> },
                    ]}
                    rows={detail.trades} rowKey={(t) => t.entry + t.symbol}
                  />
                </div>
              )}
            </SectionCard>

            {/* validation */}
            <div className="col-span-5 space-y-4">
              <SectionCard title="Walk-forward stability" className="rise">
                {detail.validation.walk_forward?.windows.length ? (
                  <ul className="space-y-1 text-[11px]">
                    {detail.validation.walk_forward.windows.map((w) => (
                      <li key={w.window} className="flex justify-between border-b border-border/40 py-1">
                        <span className="text-faint">{w.from} → {w.to}</span>
                        <span className="num">Sharpe {w.sharpe ?? "—"} · DD {fmtPct((w.max_dd ?? 0) * 100)}</span>
                      </li>
                    ))}
                    <li className="pt-1 text-dim">
                      stability: mean Sharpe {detail.validation.walk_forward.stability?.mean_sharpe ?? "—"}
                    </li>
                  </ul>
                ) : <p className="text-[12px] text-faint">{detail.validation.walk_forward?.error ?? "not run"}</p>}
              </SectionCard>
              <SectionCard title="Monte Carlo" className="rise">
                {detail.validation.monte_carlo?.median_final ? (
                  <div className="text-[11px] text-dim space-y-1">
                    <div>median final <b className="num text-text">${fmtNum(detail.validation.monte_carlo.median_final)}</b></div>
                    <div>P5 <b className="num text-neg">${fmtNum(detail.validation.monte_carlo.p5)}</b> · P95 <b className="num text-pos">${fmtNum(detail.validation.monte_carlo.p95)}</b></div>
                    <div>P(loss) <b className="num">{fmtPct(detail.validation.monte_carlo.prob_loss * 100)}</b></div>
                    <div className="text-faint">{detail.validation.monte_carlo.note}</div>
                  </div>
                ) : <p className="text-[12px] text-faint">not run</p>}
              </SectionCard>
            </div>
          </div>

          <SectionCard title="Data coverage & limitations" className="rise">
            <div className="text-[11px] text-dim space-y-1">
              {Object.entries(detail.data_snapshot).map(([s, c]) => (
                <div key={s}><b className="text-text">{s}</b>: {c.bars} bars · {c.from} → {c.to}</div>
              ))}
              <div className="pt-1 text-faint">{detail.note} · engine {detail.engine}</div>
            </div>
          </SectionCard>
        </>
      )}
    </div>
  );
}
