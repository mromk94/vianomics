"use client";

import { useEffect, useMemo, useState } from "react";

import { apiGet } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { DataTable, type Column } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { SearchInput } from "@/components/ui/search-input";
import { SectionCard } from "@/components/ui/section-card";
import { SkeletonRows } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";

interface Metrics {
  symbol: string;
  bars: number;
  latest_close: number | null;
  conventions: Record<string, unknown>;
  metrics: {
    ann_return: number | null;
    volatility: number | null;
    sharpe: number | null;
    sortino: number | null;
    max_drawdown: { max_drawdown: number } | null;
    beta: { beta: number; n: number } | null;
    correlation_spy: { rho: number; n: number } | null;
    momentum_12_1: number | null;
    fcf_yield: number | null;
    roic: number | null;
    rev_cagr: number | null;
    market_cap: number | null;
  };
  factor_scores: {
    composite: number | null;
    coverage: number;
    subscores: Record<string, number | null>;
  };
}

interface Corr {
  symbols: string[];
  matrix: (number | null)[][];
}

const PCT = (v: number | null | undefined) =>
  v == null ? "—" : `${(v * 100).toFixed(1)}%`;

export function QuantPage() {
  const [symbol, setSymbol] = useState("NVDA");
  const [m, setM] = useState<Metrics | null>(null);
  const [corr, setCorr] = useState<Corr | null>(null);
  const [rows, setRows] = useState<Metrics[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const TICKERS = ["NVDA", "MSFT", "GOOGL", "AMZN", "META", "AVGO", "TSLA", "ORCL"];

  useEffect(() => {
    setError(null);
    apiGet<Corr>(`/api/v1/quant/correlation?symbols=${TICKERS.join(",")}`)
      .then(setCorr).catch(() => {});
    Promise.all(TICKERS.map((s) => apiGet<Metrics>(`/api/v1/quant/metrics/${s}`).catch(() => null)))
      .then((r) => setRows(r.filter(Boolean) as Metrics[]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!symbol) return;
    apiGet<Metrics>(`/api/v1/quant/metrics/${symbol}`)
      .then(setM).catch((e) => setError(e.message));
  }, [symbol]);

  const heat = (v: number | null) => {
    if (v == null) return "text-faint";
    if (v > 0.7) return "bg-neg/25 text-neg";
    if (v > 0.4) return "bg-warn/20 text-warn";
    if (v > 0) return "bg-surface-2";
    return "bg-info/10 text-info";
  };

  const factorCols: Column<Metrics>[] = [
    { key: "s", header: "Ticker", render: (r) => <span className="font-semibold text-accent">{r.symbol}</span> },
    { key: "comp", header: "Composite", align: "right", render: (r) => <span className="num font-semibold">{r.factor_scores.composite != null ? fmtNum(r.factor_scores.composite, 0) : "—"}</span> },
    { key: "mom", header: "Mom", align: "right", render: (r) => <span className="num">{PCT(r.metrics.momentum_12_1)}</span> },
    { key: "vol", header: "Vol", align: "right", render: (r) => <span className="num">{PCT(r.metrics.volatility)}</span> },
    { key: "sharpe", header: "Sharpe", align: "right", render: (r) => <span className="num">{r.metrics.sharpe?.toFixed(2) ?? "—"}</span> },
    { key: "beta", header: "β", align: "right", render: (r) => <span className="num">{r.metrics.beta?.beta.toFixed(2) ?? "—"}</span> },
    { key: "mdd", header: "MaxDD", align: "right", render: (r) => <span className="num text-neg">{PCT(r.metrics.max_drawdown?.max_drawdown)}</span> },
    { key: "fcfy", header: "FCF Yld", align: "right", render: (r) => <span className="num">{PCT(r.metrics.fcf_yield)}</span> },
    { key: "roic", header: "ROIC", align: "right", render: (r) => <span className="num">{PCT(r.metrics.roic)}</span> },
    { key: "cov", header: "Cov", align: "right", render: (r) => <span className="text-[11px] text-faint">{Math.round(r.factor_scores.coverage * 5)}/5</span> },
  ];

  return (
    <div className="space-y-4">
      <PageHeader
        title="Quantitative Intelligence"
        subtitle="Factor analytics — simple daily returns · 252d annualization · rf 4% · SPY benchmark (delayed EOD)"
      />
      {error && <ErrorState title="API error" detail={error} />}

      <SectionCard title="Factor scores & risk metrics" className="rise">
        <DataTable columns={factorCols} rows={rows} loading={loading} rowKey={(r) => r.symbol} />
      </SectionCard>

      <div className="rise rise-1 grid gap-3 lg:grid-cols-2">
        {/* selected instrument detail */}
        <SectionCard title={`${symbol} — detail`}
          action={<SearchInput value={symbol} onChange={(v) => setSymbol(v.toUpperCase())} className="w-32" />}>
          {m ? (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {[
                ["Ann. return", PCT(m.metrics.ann_return)],
                ["Volatility", PCT(m.metrics.volatility)],
                ["Sharpe", m.metrics.sharpe?.toFixed(2)],
                ["Sortino", m.metrics.sortino?.toFixed(2)],
                ["Max DD", PCT(m.metrics.max_drawdown?.max_drawdown)],
                ["β vs SPY", m.metrics.beta?.beta.toFixed(2)],
                ["ρ vs SPY", m.metrics.correlation_spy?.rho.toFixed(2)],
                ["12-1 momentum", PCT(m.metrics.momentum_12_1)],
                ["FCF yield", PCT(m.metrics.fcf_yield)],
                ["ROIC", PCT(m.metrics.roic)],
                ["Rev CAGR", PCT(m.metrics.rev_cagr)],
                ["Mkt cap", m.metrics.market_cap ? `$${fmtNum(m.metrics.market_cap / 1e12, 2)}T` : "—"],
              ].map(([l, v]) => (
                <div key={l as string} className="glass-tile px-3 py-2">
                  <div className="text-[10px] tracking-wider text-dim uppercase">{l}</div>
                  <div className="num mt-0.5 font-semibold">{v ?? "—"}</div>
                </div>
              ))}
              <div className="col-span-full mt-1 text-[10px] text-faint">
                {m.bars} daily bars · composite {m.factor_scores.composite?.toFixed(0) ?? "—"}/100 · coverage {Math.round(m.factor_scores.coverage * 100)}%
              </div>
            </div>
          ) : <SkeletonRows />}
        </SectionCard>

        {/* correlation matrix */}
        <SectionCard title="Correlation matrix (vs daily returns, ~300d)">
          {corr ? (
            <div className="overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead>
                  <tr>
                    <th className="pb-1.5 text-left text-faint" />
                    {corr.symbols.map((s) => (
                      <th key={s} className="num pb-1.5 text-center text-faint">{s}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {corr.symbols.map((a, i) => (
                    <tr key={a} className="border-t border-border/40">
                      <td className="py-1 font-medium text-dim">{a}</td>
                      {corr.matrix[i].map((v, j) => (
                        <td key={j} className={`num py-1 text-center ${heat(v)}`}>
                          {v == null ? "—" : v.toFixed(2)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <EmptyState title="No price data" />}
        </SectionCard>
      </div>
    </div>
  );
}
