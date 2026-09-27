"use client";

import { useEffect, useState } from "react";
import { ShieldAlert } from "lucide-react";

import { apiGet, apiPost, ApiError } from "@/lib/api";
import { fmtNum, fmtTime } from "@/lib/format";
import { DataTable, type Column } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { SkeletonRows } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";

interface Center {
  nav: number; cash: number;
  positions: { symbol: string; sector: string; market_value: number; quantity: number }[];
  dimensions: Record<string, any>;
  limits: Record<string, number>;
  active_blocks: { rule: string; observed: number | null; required: number | string; remediation: string; timestamp: string }[];
  open_pyramid_trades: number;
  vix: number | null; fear_greed: number | null;
  engine: string; as_of: string;
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

export function RiskPage() {
  const [c, setC] = useState<Center | null>(null);
  const [checks, setChecks] = useState<Check[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [probe, setProbe] = useState({ symbol: "NVDA", notional: "25000" });
  const [probeResult, setProbeResult] = useState<Check | null>(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setError(null);
    Promise.all([
      apiGet<Center>("/api/v1/risk/center"),
      apiGet<Check[]>("/api/v1/risk/checks"),
    ]).then(([cc, ch]) => { setC(cc); setChecks(ch); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
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
    { key: "s", header: "Symbol", render: (r) => <span className="font-semibold text-accent">{r.symbol}</span> },
    { key: "n", header: "Notional", align: "right", render: (r) => <span className="num">${fmtNum(r.notional / 1000, 0)}k</span> },
    { key: "a", header: "Result", render: (r) => <StatusBadge tone={r.allowed ? "pos" : "neg"}>{r.allowed ? "allowed" : "BLOCKED"}</StatusBadge> },
    { key: "b", header: "Breaches", render: (r) => r.breaches.length ? <span className="text-[11px] text-neg">{r.breaches.map((b) => b.rule).join(", ")}</span> : <span className="text-faint">—</span> },
    { key: "t", header: "Time", align: "right", render: (r) => <span className="num text-faint text-[11px]">{fmtTime(r.checked_at)}</span> },
  ];

  return (
    <div className="space-y-4">
      <PageHeader title="Risk Center" info='Portfolio risk health — drawdown, sector and position concentration, correlation, VaR and stress tests. Breaching a limit blocks the order — risk vetoes are absolute.' subtitle={`${c?.engine ?? ""} — hard limits enforced at the gate; AI cannot override`}
        meta={c ? `NAV $${fmtNum(c.nav, 0)} · ${c.open_pyramid_trades} open pyramid trades` : undefined} />
      {notice && <div className="glass border-warn/40 p-3 text-[13px] text-warn">{notice}</div>}
      {error && <ErrorState title="API error" detail={error} onRetry={load} />}
      {loading && <SkeletonRows />}

      {c && (
        <>
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
              {Object.keys(c.dimensions.stress_tests ?? {}).length ? (
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

          {/* limits */}
          <SectionCard title="Configured limits" className="rise">
            <div className="flex flex-wrap gap-2">
              {Object.entries(c.limits).map(([k, v]) => (
                <div key={k} className="glass-tile px-3 py-1.5 text-[12px]">
                  <span className="text-dim">{k.replace(/_/g, " ")}</span>
                  <span className="num ml-2 font-semibold">{typeof v === "number" && v < 1 ? `${(v * 100).toFixed(v < 0.01 ? 2 : 0)}%` : v}</span>
                </div>
              ))}
            </div>
          </SectionCard>
        </>
      )}
    </div>
  );
}
