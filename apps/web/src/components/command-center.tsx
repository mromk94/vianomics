"use client";

import { useCallback, useEffect, useState } from "react";

import { apiGet, type CommandCenter } from "@/lib/api";
import {
  fmtCurrency,
  fmtNum,
  fmtPct,
  fmtPctPlain,
  relTime,
  signedClass,
} from "@/lib/format";
import { PageHeader } from "./ui/page-header";
import { DemoBadge, SectionCard } from "./ui/section-card";
import { MetricCard } from "./ui/metric-card";
import { DataTable, type Column } from "./ui/data-table";
import { EmptyState } from "./ui/empty-state";
import { ErrorState } from "./ui/error-state";
import { Skeleton } from "./ui/skeleton";
import { healthTone, severityTone, StatusBadge } from "./ui/status-badge";

function useCommandCenter() {
  const [data, setData] = useState<CommandCenter | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await apiGet<CommandCenter>("/api/v1/command-center"));
    } catch (e) {
      setData(null);
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return { data, error, loading, reload: load };
}

function isDemo(cc: CommandCenter, section: string) {
  return cc.demo_sections.includes(section);
}

export function CommandCenter() {
  const { data, error, loading, reload } = useCommandCenter();

  if (error && !data) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <ErrorState
          title="Cannot reach the VAIIP API"
          detail={`${error}. Start the backend (uvicorn on :8000) or check NEXT_PUBLIC_API_URL.`}
          onRetry={reload}
        />
      </div>
    );
  }

  const cc = data;

  return (
    <div>
      <PageHeader
        title="Command Center"
        description="Portfolio state, market regime, risk utilization and pending actions."
        meta={
          cc && isDemo(cc, "portfolio") ? <DemoBadge /> : undefined
        }
      />

      {/* Portfolio summary strip */}
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricCard
          label="Portfolio Value"
          loading={loading}
          value={fmtCurrency(cc?.portfolio.total_value, cc?.portfolio.currency)}
          sub={
            cc?.portfolio.daily_pnl != null
              ? `${fmtPct(cc.portfolio.daily_pnl_pct)} today`
              : undefined
          }
        />
        <MetricCard
          label="Available Cash"
          loading={loading}
          value={fmtCurrency(cc?.portfolio.cash, cc?.portfolio.currency)}
        />
        <MetricCard
          label="Daily P&L"
          loading={loading}
          tone={cc?.portfolio.daily_pnl != null && cc.portfolio.daily_pnl >= 0 ? "pos" : "neg"}
          value={
            <span className={signedClass(cc?.portfolio.daily_pnl)}>
              {fmtCurrency(cc?.portfolio.daily_pnl, cc?.portfolio.currency)}
            </span>
          }
        />
        <MetricCard
          label="Unrealized P&L"
          loading={loading}
          value={
            <span className={signedClass(cc?.portfolio.unrealized_pnl)}>
              {fmtCurrency(cc?.portfolio.unrealized_pnl, cc?.portfolio.currency)}
            </span>
          }
        />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        {/* ── Left column ── */}
        <div className="space-y-4">
          <SectionCard
            title="Portfolio Split"
            badge={cc && isDemo(cc, "split") ? <DemoBadge /> : undefined}
          >
            {loading ? (
              <Skeleton className="h-12 w-full" />
            ) : cc?.split.investment_pct != null ? (
              <div>
                <div className="mb-2 flex h-3 overflow-hidden rounded bg-surface-3">
                  <div
                    className="bg-accent"
                    style={{ width: `${cc.split.investment_pct}%` }}
                    title={`Investment ${cc.split.investment_pct}%`}
                  />
                  <div
                    className="bg-brand"
                    style={{ width: `${cc.split.trading_pct ?? 0}%` }}
                    title={`Trading ${cc.split.trading_pct}%`}
                  />
                </div>
                <div className="num flex justify-between text-xs text-dim">
                  <span>Investment {fmtPctPlain(cc.split.investment_pct)} (target 70)</span>
                  <span>Trading {fmtPctPlain(cc.split.trading_pct)} (target 30)</span>
                </div>
              </div>
            ) : (
              <EmptyState title="No portfolio" hint="Positions appear here once a portfolio exists." />
            )}
          </SectionCard>

          <SectionCard
            title="Macro Regime"
            badge={cc && isDemo(cc, "regime") ? <DemoBadge /> : undefined}
          >
            {loading ? (
              <Skeleton className="h-20 w-full" />
            ) : cc?.regime.economic_regime != null ? (
              <div className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-dim">Economic</span>
                  <StatusBadge tone="pos">{cc.regime.economic_regime}</StatusBadge>
                </div>
                <div className="flex justify-between">
                  <span className="text-dim">Market</span>
                  <StatusBadge tone="info">{cc.regime.market_regime}</StatusBadge>
                </div>
                <div className="num flex justify-between">
                  <span className="text-dim">Fear &amp; Greed</span>
                  <span>{fmtNum(cc.regime.fear_greed, 0)}</span>
                </div>
                <div className="num flex justify-between">
                  <span className="text-dim">VIX</span>
                  <span>{fmtNum(cc.regime.vix, 1)}</span>
                </div>
              </div>
            ) : (
              <EmptyState title="No regime data" hint="Available once the Macro Regime Engine (M3) is live." />
            )}
          </SectionCard>

          <SectionCard
            title="Risk Utilization"
            badge={cc && isDemo(cc, "risk") ? <DemoBadge /> : undefined}
          >
            {loading ? (
              <Skeleton className="h-20 w-full" />
            ) : cc?.risk.drawdown_pct != null ? (
              <div className="space-y-2 text-sm">
                <div className="num flex justify-between">
                  <span className="text-dim">Drawdown</span>
                  <span className={cc.risk.drawdown_pct > 10 ? "text-neg" : ""}>
                    {fmtPctPlain(cc.risk.drawdown_pct)} / {cc.risk.drawdown_limit_pct}%
                  </span>
                </div>
                <div className="num flex justify-between">
                  <span className="text-dim">Max sector</span>
                  <span className={(cc.risk.max_sector_pct ?? 0) > cc.risk.sector_limit_pct ? "text-neg" : ""}>
                    {fmtPctPlain(cc.risk.max_sector_pct)} / {cc.risk.sector_limit_pct}%
                  </span>
                </div>
                <div className="num flex justify-between">
                  <span className="text-dim">Max position</span>
                  <span className={(cc.risk.max_position_pct ?? 0) > cc.risk.position_limit_pct ? "text-neg" : ""}>
                    {fmtPctPlain(cc.risk.max_position_pct)} / {cc.risk.position_limit_pct}%
                  </span>
                </div>
                {cc.risk.warnings.length > 0 && (
                  <ul className="mt-2 space-y-1 border-t border-border pt-2">
                    {cc.risk.warnings.map((w) => (
                      <li key={w} className="text-xs text-warn">
                        ⚠ {w}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ) : (
              <EmptyState title="No positions" hint="Risk utilization appears once capital is deployed." />
            )}
          </SectionCard>
        </div>

        {/* ── Middle column ── */}
        <div className="space-y-4">
          <SectionCard
            title="Watchlist & Recent Research"
            badge={cc && isDemo(cc, "watchlist") ? <DemoBadge /> : undefined}
            padded={false}
          >
            <WatchlistTable rows={cc?.watchlist ?? []} loading={loading} />
          </SectionCard>

          <SectionCard
            title="Pending Approvals"
            badge={cc && isDemo(cc, "approvals") ? <DemoBadge /> : undefined}
          >
            {loading ? (
              <Skeleton className="h-12 w-full" />
            ) : cc && cc.approvals.length > 0 ? (
              <ul className="space-y-2">
                {cc.approvals.map((a) => (
                  <li
                    key={a.id}
                    className="flex items-center justify-between rounded border border-border bg-surface-2 px-3 py-2 text-sm"
                  >
                    <div>
                      <span className="font-medium">{a.ticker}</span>
                      <span className="ml-2 text-dim">{a.action}</span>
                      <div className="text-[11px] text-faint">
                        CIO {a.cio_rating} · Risk {a.risk_verdict} · {relTime(a.requested_at)}
                      </div>
                    </div>
                    <StatusBadge tone="warn">Awaiting human</StatusBadge>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState title="Nothing pending" hint="CIO recommendations awaiting human approval appear here." />
            )}
          </SectionCard>

          <SectionCard
            title="Active Alerts"
            badge={cc && isDemo(cc, "alerts") ? <DemoBadge /> : undefined}
          >
            {loading ? (
              <Skeleton className="h-12 w-full" />
            ) : cc && cc.alerts.length > 0 ? (
              <ul className="space-y-2">
                {cc.alerts.map((a) => (
                  <li key={a.id} className="flex items-start gap-2 text-sm">
                    <StatusBadge tone={severityTone(a.severity)} dot>
                      {a.severity}
                    </StatusBadge>
                    <div>
                      <div>{a.message}</div>
                      <div className="text-[11px] text-faint">
                        {a.source} · {relTime(a.at)}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState title="No active alerts" hint="Monitoring thresholds are clear." />
            )}
          </SectionCard>
        </div>

        {/* ── Right column ── */}
        <div className="space-y-4">
          <SectionCard
            title="Recent Decisions"
            badge={cc && isDemo(cc, "decisions") ? <DemoBadge /> : undefined}
          >
            {loading ? (
              <Skeleton className="h-12 w-full" />
            ) : cc && cc.decisions.length > 0 ? (
              <ul className="space-y-2">
                {cc.decisions.map((d) => (
                  <li
                    key={d.id}
                    className="flex items-center justify-between text-sm"
                  >
                    <div>
                      <span className="font-medium">{d.ticker}</span>
                      <div className="text-[11px] text-faint">
                        {relTime(d.at)} · confidence {d.cio_confidence ?? "—"}%
                      </div>
                    </div>
                    <StatusBadge
                      tone={
                        d.verdict === "APPROVED"
                          ? "pos"
                          : d.verdict === "WATCHLIST"
                            ? "info"
                            : "neutral"
                      }
                    >
                      {d.verdict}
                    </StatusBadge>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState title="No decisions yet" hint="Investment decision records land in the Decision Journal." />
            )}
          </SectionCard>

          <SectionCard title="Data Provider Health" padded={false}>
            <ProviderTable rows={cc?.providers ?? []} loading={loading} />
          </SectionCard>
        </div>
      </div>
    </div>
  );
}

/* ── tables ── */

type WatchRow = CommandCenter["watchlist"][number];
const watchCols: Column<WatchRow>[] = [
  { key: "ticker", header: "Ticker", render: (r) => <span className="font-medium">{r.ticker}</span> },
  { key: "gz", header: "GZ", align: "right", render: (r) => (r.green_zone_score != null ? `${r.green_zone_score}/20` : "—") },
  { key: "price", header: "Price", align: "right", render: (r) => fmtCurrency(r.last_price) },
  {
    key: "chg",
    header: "1D",
    align: "right",
    render: (r) => <span className={signedClass(r.change_pct)}>{fmtPct(r.change_pct)}</span>,
  },
];

function WatchlistTable({ rows, loading }: { rows: WatchRow[]; loading: boolean }) {
  return (
    <DataTable
      columns={watchCols}
      rows={rows}
      loading={loading}
      rowKey={(r) => r.ticker}
      empty="Watchlist is empty"
    />
  );
}

type ProviderRow = CommandCenter["providers"][number];
const providerCols: Column<ProviderRow>[] = [
  { key: "name", header: "Provider", render: (r) => r.name },
  {
    key: "status",
    header: "Status",
    render: (r) => <StatusBadge tone={healthTone(r.status)} dot>{r.status}</StatusBadge>,
  },
  {
    key: "sync",
    header: "Last sync",
    align: "right",
    render: (r) => <span className="text-xs text-dim">{relTime(r.last_sync)}</span>,
  },
];

function ProviderTable({ rows, loading }: { rows: ProviderRow[]; loading: boolean }) {
  return (
    <DataTable
      columns={providerCols}
      rows={rows}
      loading={loading}
      rowKey={(r) => r.name}
      empty="No providers configured"
    />
  );
}
