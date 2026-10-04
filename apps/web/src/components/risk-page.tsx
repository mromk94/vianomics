"use client";

import { useEffect, useState } from "react";
import { ShieldAlert } from "lucide-react";

import { apiGet, apiPost, apiPut, ApiError } from "@/lib/api";
import { fmtNum, fmtTime } from "@/lib/format";
import { DataTable, type Column } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { Sym } from "@/components/symbol-drawer";
import { PyramidModal } from "@/components/pyramid-modal";
import { SectionCard } from "@/components/ui/section-card";
import { SkeletonRows } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";

interface Center {
  nav: number; cash: number;
  positions: { symbol: string; sector: string; market_value: number;
    quantity: number; avg_cost?: number; unrealized?: number;
    daily_pnl?: number | null; price_vs_iv?: number }[];
  dimensions: Record<string, any>;
  limits: Record<string, number>;
  active_blocks: { rule: string; observed: number | null; required: number | string; remediation: string; timestamp: string }[];
  open_pyramid_trades: number;
  pyramid_trades: { id: string; symbol: string; state: string;
    entry: number; shares: number; stop: number; target1: number;
    additions: number; atr_initial?: number | null;
    atr_current?: number | null; signal_engine?: string | null;
    created_at?: string | null;
    events?: { t?: string; event?: string; reason?: string }[] }[];
  monitors: { symbol: string; weight: number;
    price_vs_iv: number | null; tests: Record<string, string>;
    actionable: Record<string, string> }[];
  external: { label: string; source: string; equity: number;
    balance: number; unrealized: number;
    positions: { symbol: string; qty: number; price: number;
      profit?: number }[];
    mdd_pct: number | null; var_95: number | null;
    daily_pnl: number | null; synced_at: string | null;
    stale: boolean }[];
  dashboard?: {
    equity: number; gross_notional: number; net_notional: number;
    gross_leverage: number; net_leverage: number;
    initial_margin: number; current_margin: number;
    maintenance_margin: number; free_margin: number;
    margin_headroom: number; margin_utilisation: number;
    open_stop_risk: number; unstopped_notional: number;
    open_risk: number; open_risk_pct_equity: number;
    net_pnl: number; remaining_reward: number;
    portfolio_rr: number | null; risk_capacity: number;
    risk_by_strategy: Record<string, number>;
    risk_by_sector: Record<string, number>;
    status: string; warnings: string[]; pm_action: string };
  margin?: { used: number; utilisation: number;
    call_buffer_check: { margin_call_before_stop: boolean;
      warning: string | null } | null };
  drawdown?: { max_dd: number | null;
    escalation: { drawdown: number; level: string;
      action: string } };
  named_stress?: { scenario: string; key: string; shock: number;
    scope: string; pnl: number; loss: number;
    loss_pct_equity: number; status: string }[];
  vix: number | null; fear_greed: number | null;
  engine: string; as_of: string;
}

interface PmRow {
  symbol: string; display_symbol?: string | null;
  source?: string | null; weight: number;
  checks: Record<string, string>;
  pm_decision: string; reason: string;
  open_risk: number; unstopped: boolean;
}

interface PmBook {
  positions: PmRow[];
  portfolio: { status: string; pm_action: string;
    risk_capacity: number; open_risk: number;
    margin_utilisation: number };
}

interface Check {
  id: string; symbol: string; side: string; notional: number;
  allowed: boolean; breaches: { rule: string; observed: number | null; required: number | string; remediation: string }[];
  checked_at: string;
}

const DIM_LABEL: Record<string, string> = {
  fundamental_valuation: "Fundamental & Valuation",
  concentration: "Position & Concentration",
  factor_correlation: "Factor & Correlation",
  leverage_margin: "Leverage & Margin",
  liquidity: "Liquidity & Financing",
  volatility_structure: "Volatility & Structure",
  feedback_path: "Feedback & Path Dep.",
};

const DIM_TONE: Record<string, "pos" | "warn" | "neg" | "info"> = {
  ok: "pos", review: "warn", degraded: "warn", breach: "neg", unknown: "info",
};

const PM_TONE: Record<string, "pos" | "warn" | "neg" | "info"> = {
  ENTER: "pos", ADD: "pos", HOLD: "info", REVIEW: "warn",
  REDUCE: "warn", EXIT: "neg", BLOCK: "neg",
};

const DD_TONE: Record<string, "pos" | "warn" | "neg" | "info"> = {
  NORMAL: "pos", WATCH: "warn", RISK_REDUCTION: "warn",
  TRADING_HALT: "neg",
};

export function RiskPage() {
  const [c, setC] = useState<Center | null>(null);
  const [pm, setPm] = useState<PmBook | null>(null);
  const [checks, setChecks] = useState<Check[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [probe, setProbe] = useState({ symbol: "NVDA", notional: "25000" });
  const [probeResult, setProbeResult] = useState<Check | null>(null);
  const [loading, setLoading] = useState(true);
  const [limitsCfg, setLimitsCfg] = useState<{ version: number; limits: Record<string, number>; source: string } | null>(null);
  const [limEdit, setLimEdit] = useState<Record<string, string>>({});
  const [limEditing, setLimEditing] = useState(false);
  const [pyrOpen, setPyrOpen] = useState(false);

  const load = () => {
    setError(null);
    Promise.all([
      apiGet<Center>("/api/v1/risk/center"),
      apiGet<Check[]>("/api/v1/risk/checks"),
      apiGet<{ version: number; limits: Record<string, number>; source: string }>("/api/v1/risk/limits"),
    ]).then(([cc, ch, lc]) => { setC(cc); setChecks(ch); setLimitsCfg(lc); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
    apiGet<PmBook>("/api/v1/pm/positions")
      .then(setPm).catch(() => setPm(null));
  };
  useEffect(load, []);

  const runProbe = async () => {
    try {
      const r = await apiPost<Check & { allowed: boolean }>(
        "/api/v1/risk/check-order",
        { symbol: probe.symbol, side: "buy", notional: Number(probe.notional) });
      setProbeResult(r);
      setNotice(null);
      load();
    } catch (e) {
      setNotice(e instanceof ApiError && e.status === 401
        ? "Sign in (Settings) to run order checks."
        : (e as Error).message);
    }
  };

  const stressCols: Column<{ shock: string; pnl: number; nav_after: number }>[] = [
    { key: "s", header: "Shock", render: (r) => <span className="num">{r.shock}</span> },
    { key: "p", header: "P&L", align: "right", render: (r) => <span className="num text-neg">${fmtNum(r.pnl / 1000, 1)}k</span> },
    { key: "n", header: "NAV after", align: "right", render: (r) => <span className="num">${fmtNum(r.nav_after / 1000, 0)}k</span> },
  ];

  const checkCols: Column<Check>[] = [
    { key: "s", header: "Symbol", render: (r) => <Sym s={r.symbol} /> },
    { key: "n", header: "Notional", align: "right", render: (r) => <span className="num">${fmtNum(r.notional / 1000, 0)}k</span> },
    { key: "a", header: "Result", render: (r) => <StatusBadge tone={r.allowed ? "pos" : "neg"}>{r.allowed ? "allowed" : "BLOCKED"}</StatusBadge> },
    { key: "b", header: "Breaches", render: (r) => r.breaches.length ? <span className="text-[11px] text-neg">{r.breaches.map((b) => b.rule).join(", ")}</span> : <span className="text-faint">—</span> },
    { key: "t", header: "Time", align: "right", render: (r) => <span className="num text-faint text-[11px]">{fmtTime(r.checked_at)}</span> },
  ];

  return (
    <div className="space-y-4">
      <PageHeader title="Risk Center" info='Portfolio risk health — drawdown, sector and position concentration, correlation, VaR and stress tests. Breaching a limit blocks the order — risk vetoes are absolute.' subtitle={`${c?.engine ?? ""} — hard limits enforced at the gate; AI cannot override`}
        meta={c ? `NAV $${fmtNum(c.nav, 0)} · ${c.open_pyramid_trades} open pyramid trades` : undefined}
        actions={
          <button onClick={() => setPyrOpen(true)}
            className="rounded-full bg-accent px-4 py-1.5 text-[12px] font-semibold text-[#0b0f1a] hover:brightness-110">
            Pyramid calculator
          </button>} />
      {notice && <div className="glass border-warn/40 p-3 text-[13px] text-warn">{notice}</div>}
      {error && <ErrorState title="API error" detail={error} onRetry={load} />}
      {loading && <SkeletonRows />}

      {c && (
        <>
          {/* portfolio dashboard — Trade Risk Sheet → Ledger → Dashboard → PM */}
          {c.dashboard && (
            <SectionCard title="Portfolio risk dashboard" className="rise"
              action={
                <div className="flex items-center gap-2">
                  {c.drawdown?.escalation && (
                    <StatusBadge tone={DD_TONE[c.drawdown.escalation.level] ?? "info"}>
                      DD {c.drawdown.escalation.level.replace(/_/g, " ")}
                    </StatusBadge>
                  )}
                  <StatusBadge tone={c.dashboard.status === "NORMAL" ? "pos" : c.dashboard.status === "REDUCE" ? "neg" : "warn"}>
                    {c.dashboard.status}
                  </StatusBadge>
                </div>
              }>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-7">
                {([
                  ["Gross lev.", `${c.dashboard.gross_leverage.toFixed(2)}×`, c.dashboard.gross_leverage > 1 ? "text-warn" : ""],
                  ["Margin util.", `${(c.dashboard.margin_utilisation * 100).toFixed(1)}%`, c.dashboard.margin_utilisation > 0.5 ? "text-neg" : c.dashboard.margin_utilisation > 0.3 ? "text-warn" : ""],
                  ["Open risk", c.dashboard.open_risk_pct_equity != null ? `${(c.dashboard.open_risk_pct_equity * 100).toFixed(1)}%` : "—", ""],
                  ["Risk capacity", `$${fmtNum(c.dashboard.risk_capacity, 0)}`, c.dashboard.risk_capacity <= 0 ? "text-neg" : "text-pos"],
                  ["Stop risk", `$${fmtNum(c.dashboard.open_stop_risk, 0)}`, ""],
                  ["Reward left", `$${fmtNum(c.dashboard.remaining_reward, 0)}`, "text-pos"],
                  ["Port. R/R", c.dashboard.portfolio_rr != null ? `${c.dashboard.portfolio_rr.toFixed(1)}` : "—", ""],
                ] as [string, string, string][]).map(([label, val, tone]) => (
                  <div key={label} className="glass-tile px-3 py-2">
                    <div className="text-[10px] uppercase tracking-wider text-faint">{label}</div>
                    <div className={`num mt-0.5 text-[15px] font-semibold ${tone}`}>{val}</div>
                  </div>
                ))}
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-dim">
                <span>PM action: <b className="text-text">{c.dashboard.pm_action}</b></span>
                {c.dashboard.unstopped_notional > 0 && (
                  <span className="text-warn">${fmtNum(c.dashboard.unstopped_notional, 0)} notional unstopped</span>
                )}
                {(c.dashboard.warnings ?? []).map((w, i) => (
                  <span key={i} className="text-warn">{w}</span>
                ))}
                {c.margin?.call_buffer_check?.margin_call_before_stop && (
                  <span className="text-neg">{c.margin.call_buffer_check.warning}</span>
                )}
                {c.drawdown?.escalation && c.drawdown.escalation.level !== "NORMAL" && (
                  <span className="text-warn">→ {c.drawdown.escalation.action}</span>
                )}
              </div>
            </SectionCard>
          )}

          {/* 7 dimensions */}
          <SectionCard title="Seven risk dimensions" className="rise">
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
              {Object.entries(DIM_LABEL).map(([k, label]) => {
                const d = c.dimensions[k] ?? {};
                return (
                  <div key={k} className="glass-tile px-3 py-2.5">
                    <div className="flex items-center justify-between">
                      <span className="text-[12px] text-dim">{label}</span>
                      <StatusBadge tone={DIM_TONE[d.status] ?? "info"}>{d.status ?? "unknown"}</StatusBadge>
                    </div>
                    <div className="num mt-1 text-[11px] text-faint">
                      {d.max_single_name_pct != null && `max name ${(d.max_single_name_pct * 100).toFixed(1)}%`}
                      {d.max_sector_pct != null && ` · max sector ${(d.max_sector_pct * 100).toFixed(0)}%`}
                      {d.gross != null && `gross ${d.gross}×`}
                      {d.avg_correlation != null && `avg ρ ${d.avg_correlation.toFixed(2)}`}
                      {d.illiquid_weight != null && `illiq ${(d.illiquid_weight * 100).toFixed(0)}%`}
                      {d.max_drawdown != null && `MDD ${(d.max_drawdown * 100).toFixed(1)}%`}
                      {d.detail ?? ""}
                    </div>
                  </div>
                );
              })}
            </div>
          </SectionCard>

          <div className="rise rise-1 grid gap-3 lg:grid-cols-2">
            {/* stress tests */}
            <SectionCard title="Stress tests">
              {(c.named_stress?.length ?? 0) > 0 ? (
                <div className="max-h-52 space-y-1 overflow-y-auto pr-1">
                  {c.named_stress!.map((s) => (
                    <div key={s.key} className="flex items-center justify-between rounded-lg border border-border/60 px-2.5 py-1.5 text-[12px]">
                      <div className="min-w-0">
                        <div className="truncate text-text">{s.scenario}</div>
                        <div className="num text-[10px] text-faint">{(s.shock * 100).toFixed(0)}% {s.scope}</div>
                      </div>
                      <div className="flex items-center gap-2">
                        <div className="text-right">
                          <div className="num text-neg">−${fmtNum(s.loss, 0)}</div>
                          <div className="num text-[10px] text-faint">{(s.loss_pct_equity * 100).toFixed(1)}% eq</div>
                        </div>
                        <StatusBadge tone={s.status === "OK" ? "pos" : s.status === "WATCH" ? "warn" : "neg"}>{s.status}</StatusBadge>
                      </div>
                    </div>
                  ))}
                </div>
              ) : Object.keys(c.dimensions.stress_tests ?? {}).length ? (
                <DataTable
                  columns={stressCols}
                  rows={Object.entries(c.dimensions.stress_tests).map(([s, v]: [string, any]) => ({ shock: s, ...v }))}
                  rowKey={(r) => r.shock}
                />
              ) : <EmptyState title="No positions to stress" />}
              <div className="mt-2 flex gap-4 text-[11px] text-faint">
                <span>VIX <b className="num text-text">{c.vix ?? "—"}</b></span>
                <span>F&G <b className="num text-text">{c.fear_greed ?? "—"}</b></span>
                <span>Cash <b className="num text-text">${fmtNum(c.cash, 0)}</b></span>
              </div>
            </SectionCard>

            {/* active blocks */}
            <SectionCard title={`Active risk blocks (${c.active_blocks.length})`}>
              {c.active_blocks.length === 0 ? (
                <EmptyState title="No active blocks" hint="All portfolio-level limits currently satisfied." />
              ) : (
                <ul className="space-y-2">
                  {c.active_blocks.map((b, i) => (
                    <li key={i} className="rounded-xl border border-neg/30 bg-neg/5 p-3">
                      <div className="flex items-center justify-between">
                        <StatusBadge tone="neg"><ShieldAlert className="mr-1 size-3" />{b.rule}</StatusBadge>
                        <span className="num text-[11px] text-faint">{fmtTime(b.timestamp)}</span>
                      </div>
                      <div className="mt-1 text-[12px]">
                        observed <b className="num text-neg">{b.observed}</b> · required <b className="num">{String(b.required)}</b>
                      </div>
                      <div className="text-[11px] text-dim">→ {b.remediation}</div>
                    </li>
                  ))}
                </ul>
              )}
            </SectionCard>
          </div>

          {/* order-gate probe + history */}
          <div className="rise rise-2 grid gap-3 lg:grid-cols-2">
            <SectionCard title="Order gate — dry run" action="enforced before any approval">
              <div className="flex gap-2">
                <input value={probe.symbol} onChange={(e) => setProbe({ ...probe, symbol: e.target.value.toUpperCase() })}
                  className="w-24 rounded-md border border-border bg-surface-2 px-2.5 py-1.5 text-[13px] font-semibold uppercase text-text" />
                <input value={probe.notional} onChange={(e) => setProbe({ ...probe, notional: e.target.value })}
                  className="num w-32 rounded-md border border-border bg-surface-2 px-2.5 py-1.5 text-[13px] text-text" placeholder="Notional $" />
                <button onClick={runProbe}
                  className="rounded-full bg-accent px-4 py-1.5 text-[13px] font-semibold text-[#0b0f1a] hover:brightness-110">Check</button>
              </div>
              {probeResult && (
                <div className="mt-3">
                  <StatusBadge tone={probeResult.allowed ? "pos" : "neg"}>
                    {probeResult.allowed ? "order allowed" : "ORDER BLOCKED"}
                  </StatusBadge>
                  {probeResult.breaches.map((b, i) => (
                    <div key={i} className="mt-1.5 text-[12px] text-dim">
                      <span className="text-neg">{b.rule}</span> — observed {b.observed} vs required {String(b.required)} → {b.remediation}
                    </div>
                  ))}
                </div>
              )}
            </SectionCard>

            <SectionCard title="Gate history">
              {checks.length ? (
                <div className="max-h-64 overflow-y-auto">
                  <DataTable columns={checkCols} rows={checks} rowKey={(r) => r.id} />
                </div>
              ) : <EmptyState title="No checks yet" />}
            </SectionCard>
          </div>

          {/* external accounts (MT4 etc.) — real equity risk stats */}
          {c.external?.length > 0 && (
            <SectionCard title="External accounts" className="rise rise-2" action="pushed by EA bridge">
              <div className="grid gap-2 sm:grid-cols-2">
                {c.external.map((a) => (
                  <div key={a.label} className={`glass-tile p-3 ${a.stale ? "border border-warn/40" : ""}`}>
                    <div className="flex items-center justify-between">
                      <span className="text-[13px] font-semibold">{a.label}</span>
                      <StatusBadge tone={a.stale ? "warn" : "pos"}>{a.stale ? "stale" : "live"}</StatusBadge>
                    </div>
                    <div className="num mt-1.5 grid grid-cols-4 gap-2 text-[12px]">
                      <div><div className="text-[10px] text-faint">EQUITY</div>${fmtNum(a.equity, 0)}</div>
                      <div><div className="text-[10px] text-faint">UNREAL.</div><span className={a.unrealized >= 0 ? "text-pos" : "text-neg"}>{a.unrealized >= 0 ? "+" : ""}{fmtNum(a.unrealized, 0)}</span></div>
                      <div><div className="text-[10px] text-faint">MDD</div>{a.mdd_pct != null ? `${(a.mdd_pct * 100).toFixed(1)}%` : "—"}</div>
                      <div><div className="text-[10px] text-faint">VAR95</div>{a.var_95 != null ? `$${fmtNum(a.var_95, 0)}` : "—"}</div>
                    </div>
                    {a.positions.length > 0 && (
                      <div className="mt-2 border-t border-border pt-1.5 text-[11px] text-dim">
                        {a.positions.slice(0, 6).map((p2, i) => (
                          <div key={i} className="flex justify-between py-0.5">
                            <Sym s={p2.symbol} />
                            <span className="num">×{p2.qty} @ {p2.price}</span>
                            <span className={`num ${(p2.profit ?? 0) >= 0 ? "text-pos" : "text-neg"}`}>{p2.profit != null ? `${p2.profit >= 0 ? "+" : ""}${fmtNum(p2.profit, 0)}` : "—"}</span>
                          </div>
                        ))}
                        {a.positions.length > 6 && <div className="text-faint">+{a.positions.length - 6} more</div>}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </SectionCard>
          )}

          {/* positions + holding monitor */}
          <div className="rise rise-2 grid gap-3 lg:grid-cols-2">
            <SectionCard title="Positions" action={`${c.positions.length} open`}>
              {c.positions.length === 0 ? (
                <EmptyState title="No internal positions" hint="Internal book empty — MT4/external equity shown above." />
              ) : (
                <>
                <div className="max-h-64 overflow-y-auto">
                  <table className="w-full text-[12px]">
                    <thead><tr className="border-b border-border text-left text-[10px] tracking-wider text-dim uppercase">
                      <th className="py-1">Symbol</th><th>Sector</th><th className="text-right">Value</th><th className="text-right">Unreal.</th><th className="text-right">vs IV</th><th className="text-right">PM</th>
                    </tr></thead>
                    <tbody>
                      {c.positions.map((pp) => {
                        const p = pm?.positions.find((r) => r.symbol === pp.symbol || r.display_symbol === pp.symbol);
                        return (
                        <tr key={pp.symbol} className="border-b border-border/50 last:border-0">
                          <td className="py-1.5"><Sym s={pp.symbol} />{p?.unstopped && <span className="ml-1 text-[9px] text-warn" title="no stop set">unstopped</span>}</td>
                          <td className="text-dim">{pp.sector}</td>
                          <td className="num text-right">${fmtNum(pp.market_value, 0)}</td>
                          <td className={`num text-right ${(pp.unrealized ?? 0) >= 0 ? "text-pos" : "text-neg"}`}>{pp.unrealized != null ? `${pp.unrealized >= 0 ? "+" : ""}$${fmtNum(pp.unrealized, 0)}` : "—"}</td>
                          <td className={`num text-right ${(pp.price_vs_iv ?? 0) > 0 ? "text-neg" : "text-pos"}`}>{pp.price_vs_iv != null ? `${pp.price_vs_iv >= 0 ? "+" : ""}${(pp.price_vs_iv * 100).toFixed(0)}%` : "—"}</td>
                          <td className="text-right">{p ? <StatusBadge tone={PM_TONE[p.pm_decision] ?? "info"}>{p.pm_decision}</StatusBadge> : <span className="text-faint">—</span>}</td>
                        </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                {pm && pm.positions.some((x) => x.reason) && (
                  <div className="mt-2 space-y-0.5 border-t border-border pt-1.5 text-[10px] text-dim">
                    {pm.positions.filter((x) => x.reason && x.pm_decision !== "HOLD").map((x) => (
                      <div key={x.symbol}><b className="text-text">{x.display_symbol ?? x.symbol}</b>: {x.reason}</div>
                    ))}
                  </div>
                )}
                </>
              )}
            </SectionCard>

            <SectionCard title="Holding monitor" action="Part 13 — 5 tests, no mechanical stops">
              {!c.monitors?.length ? (
                <EmptyState title="Nothing to monitor" hint="Runs on internal investment positions." />
              ) : (
                <div className="space-y-2">
                  {c.monitors.map((m2) => (
                    <div key={m2.symbol} className="glass-tile p-2.5">
                      <div className="flex items-center justify-between text-[12px]">
                        <Sym s={m2.symbol} />
                        <span className="num text-dim">{(m2.weight * 100).toFixed(1)}%</span>
                      </div>
                      {Object.keys(m2.actionable).length === 0 ? (
                        <span className="text-[11px] text-pos">all tests pass</span>
                      ) : (
                        Object.entries(m2.actionable).map(([k, v]) => (
                          <div key={k} className="mt-1 text-[11px]">
                            <span className="text-warn">{k.replace(/_/g, " ")}</span>
                            <span className="ml-1.5 text-neg font-medium">{v.replace(/_/g, " ")}</span>
                          </div>
                        ))
                      )}
                    </div>
                  ))}
                </div>
              )}
            </SectionCard>
          </div>

          {/* pyramid state machine */}
          <SectionCard title={`Pyramid trades (${c.open_pyramid_trades})`} className="rise rise-3"
            action="adaptive ATR state machine — eligible→position→trailing→exit">
            {!c.pyramid_trades?.length ? (
              <EmptyState title="No pyramid candidates or trades"
                hint="Trade-eligible pyramids appear when the technical scan fires entry_signal; promotion to Position 1 requires approval." />
            ) : (
              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                {c.pyramid_trades.map((t) => {
                  const eligible = t.state === "trade_eligible";
                  const lastEv = t.events?.[t.events.length - 1]?.event;
                  return (
                    <div key={t.id} className="glass-tile p-3 text-[12px]">
                      <div className="flex items-center justify-between">
                        <Sym s={t.symbol} />
                        <StatusBadge tone={eligible ? "warn" : t.state === "closed" ? "neg" : "pos"}>
                          {eligible ? "eligible" : t.state}
                        </StatusBadge>
                      </div>
                      <div className="num mt-1.5 grid grid-cols-3 gap-1 text-[11px]">
                        <div><div className="text-[10px] text-faint">ENTRY</div>${fmtNum(t.entry, 2)}</div>
                        <div><div className="text-[10px] text-faint">STOP 1.5×ATR</div><span className="text-neg">${fmtNum(t.stop, 2)}</span></div>
                        <div><div className="text-[10px] text-faint">TGT 3×ATR</div><span className="text-pos">${fmtNum(t.target1, 2)}</span></div>
                      </div>
                      <div className="num mt-1 flex justify-between text-[11px] text-dim">
                        <span>shares {t.shares}{t.additions > 0 ? ` · +${t.additions} adds` : ""}</span>
                        <span className="text-faint">ATR {fmtNum(t.atr_current ?? t.atr_initial, 2)}</span>
                      </div>
                      <div className="mt-0.5 text-[10px] text-faint">
                        {t.signal_engine ? `${t.signal_engine} signal` : "manual"}
                        {lastEv ? ` · ${lastEv}` : ""}
                        {t.created_at ? ` · ${fmtTime(t.created_at)}` : ""}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </SectionCard>

          {/* limits — editable, versioned, enforced at the gate */}
          <SectionCard title={`Configured limits ${limitsCfg ? `· v${limitsCfg.version} (${limitsCfg.source})` : ""}`} className="rise"
            action={
              <button onClick={() => {
                        setLimEditing((x) => !x);
                        if (limitsCfg && !limEditing) {
                          const f: Record<string, string> = {};
                          for (const [k, v] of Object.entries(limitsCfg.limits)) f[k] = String(v);
                          setLimEdit(f);
                        }
                      }}
                className="rounded-full border border-border px-3 py-1 text-[11px] text-dim hover:text-text">
                {limEditing ? "Cancel" : "Edit limits"}
              </button>
            }>
            {!limEditing ? (
              <div className="flex flex-wrap gap-2">
                {Object.entries(c.limits).map(([k, v]) => (
                  <div key={k} className="glass-tile px-3 py-1.5 text-[12px]">
                    <span className="text-dim">{k.replace(/_/g, " ")}</span>
                    <span className="num ml-2 font-semibold">{typeof v === "number" && v < 1 ? `${(v * 100).toFixed(v < 0.01 ? 2 : 0)}%` : v}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {Object.keys(limitsCfg?.limits ?? c.limits).map((k) => (
                    <label key={k} className="text-[11px] text-dim">
                      {k.replace(/_/g, " ")}
                      <input value={limEdit[k] ?? ""} onChange={(e) => setLimEdit({ ...limEdit, [k]: e.target.value })}
                        className="num mt-0.5 w-full rounded-md border border-border bg-surface-2 px-2 py-1 text-[12px] text-text" />
                    </label>
                  ))}
                </div>
                <button
                  onClick={async () => {
                    try {
                      const body: Record<string, unknown> = {};
                      for (const [k, v] of Object.entries(limEdit)) {
                        const n = Number(v);
                        if (!Number.isNaN(n)) body[k] = n;
                      }
                      const r = await apiPut<{ version: number; limits: Record<string, number> }>("/api/v1/risk/limits", body);
                      setLimitsCfg({ ...r, source: "configured" });
                      setLimEditing(false);
                      setNotice(`Limits updated — v${r.version} now enforced at the gate.`);
                      load();
                    } catch (e) {
                      setNotice(e instanceof Error ? e.message : "save failed");
                    }
                  }}
                  className="mt-3 rounded-full bg-accent px-4 py-1.5 text-[12px] font-semibold text-[#0b0f1a]">
                  Save as new version
                </button>
              </div>
            )}
          </SectionCard>
        </>
      )}
      <PyramidModal open={pyrOpen} onClose={() => setPyrOpen(false)}
        onCreated={load} />
    </div>
  );
}
