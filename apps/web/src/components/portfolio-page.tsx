"use client";

import { useEffect, useState } from "react";

import { apiGet } from "@/lib/api";
import { fmtNum, fmtPct } from "@/lib/format";
import { DataTable } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { MetricCard } from "@/components/ui/metric-card";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { SkeletonRows } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";

interface Positions {
  nav: number | null; cash: number; as_of: string; note: string;
  positions: { symbol: string; sector: string; quantity: number;
    market_value: number; weight_pct: number | null;
    source?: string; external?: boolean;
    display_symbol?: string; unrealized?: number }[];
}
interface Attr {
  positions: { symbol: string; qty: number; avg_fill: number;
    last: number; unrealized_pnl: number; source: string }[];
  note: string;
}

export function PortfolioPage() {
  const [pos, setPos] = useState<Positions | null>(null);
  const [attr, setAttr] = useState<Attr | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiGet<Positions>("/api/v1/portfolio/positions").then(setPos)
      .catch((e) => setError(e.message));
    apiGet<Attr>("/api/v1/feedback/attribution").then(setAttr)
      .catch(() => {});
  }, []);

  const gross = pos?.positions.reduce((s, p) => s + p.market_value, 0) ?? 0;

  return (
    <div className="space-y-4">
      <PageHeader title="Portfolio" info='Shows what the system currently owns (positions), how much each holding is worth, and its weight in the account. Fills from approved orders land here automatically — this is the book, not a broker statement.' subtitle="aggregate book — ledger fills + connected accounts (MT4, Bamboo) as per-position holdings" />
      {error && <ErrorState title="API error" detail={error} />}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricCard label="NAV" value={pos?.nav ? `$${fmtNum(pos.nav)}` : "—"} />
        <MetricCard label="Gross exposure" value={pos?.positions.length ? `$${fmtNum(gross)}` : "—"} />
        <MetricCard label="Cash" value={pos?.positions.length ? `$${fmtNum(pos.cash)}` : "—"} />
        <MetricCard label="Holdings" value={pos ? String(pos.positions.length) : "—"} />
      </div>

      <SectionCard title="Positions" className="rise">
        {!pos ? <SkeletonRows /> : pos.positions.length === 0 ? (
          <EmptyState title="Empty book" hint="Approved orders fill through the execution layer and book here automatically." />
        ) : (
          <DataTable
            columns={[
              { key: "s", header: "Symbol", render: (p: Positions["positions"][0]) => (
                <span>
                  <span className="font-semibold text-accent">{p.display_symbol ?? p.symbol}</span>
                  {p.source && p.source !== "ledger" && <StatusBadge tone="info">{p.source}</StatusBadge>}
                </span>) },
              { key: "sec", header: "Sector", render: (p) => p.sector },
              { key: "q", header: "Qty", align: "right", render: (p) => <span className="num">{fmtNum(p.quantity, 0)}</span> },
              { key: "m", header: "Mkt value", align: "right", render: (p) => <span className="num">${fmtNum(p.market_value)}</span> },
              { key: "w", header: "Weight", align: "right", render: (p) => <span className="num">{fmtPct(p.weight_pct ?? 0)}</span> },
              { key: "u", header: "Unreal.", align: "right", render: (p) => p.unrealized != null ? (
                <span className={`num ${p.unrealized >= 0 ? "text-pos" : "text-neg"}`}>{p.unrealized >= 0 ? "+" : ""}${fmtNum(p.unrealized)}</span>) : "—" },
            ]}
            rows={pos.positions} rowKey={(p) => p.symbol}
          />
        )}
        {pos && <p className="mt-2 text-[10px] text-faint">{pos.note}</p>}
      </SectionCard>

      <SectionCard title="Fill attribution" className="rise" action="unrealized vs last close">
        {!attr || attr.positions.length === 0 ? (
          <EmptyState title="No fills" hint="Paper fills appear here with source labels." />
        ) : (
          <DataTable
            columns={[
              { key: "s", header: "Symbol", render: (p: Attr["positions"][0]) => <span className="font-semibold">{p.symbol}</span> },
              { key: "src", header: "Source", render: (p) => <StatusBadge tone="info">{p.source}</StatusBadge> },
              { key: "f", header: "Avg fill", align: "right", render: (p) => <span className="num">${fmtNum(p.avg_fill, 2)}</span> },
              { key: "l", header: "Last", align: "right", render: (p) => <span className="num">${fmtNum(p.last, 2)}</span> },
              { key: "u", header: "Unrealized", align: "right", render: (p) => <span className={`num ${p.unrealized_pnl >= 0 ? "text-pos" : "text-neg"}`}>${fmtNum(p.unrealized_pnl)}</span> },
            ]}
            rows={attr.positions} rowKey={(p) => p.symbol + p.source}
          />
        )}
        {attr && <p className="mt-2 text-[10px] text-faint">{attr.note}</p>}
      </SectionCard>
    </div>
  );
}
