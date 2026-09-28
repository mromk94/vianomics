"use client";

import { useEffect, useState } from "react";

import { apiGet } from "@/lib/api";
import { fmtPct } from "@/lib/format";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { SkeletonRows } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";

interface Alloc {
  mandate_targets: { investment_pct: number; trading_pct: number;
    max_stocks_investment: number; max_stocks_trading: number;
    max_drawdown_pct: number };
  actual: { nav: number | null;
    sectors: { sector: string; weight_pct: number }[];
    largest_position_pct: number | null };
  note: string;
}

export function AllocationPage() {
  const [a, setA] = useState<Alloc | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    apiGet<Alloc>("/api/v1/portfolio/allocation").then(setA)
      .catch((e) => setError(e.message));
  }, []);

  const Bar = ({ label, pct, tone = "#7c9cff" }: { label: string; pct: number; tone?: string }) => (
    <div className="mb-2">
      <div className="mb-0.5 flex justify-between text-[11px]">
        <span className="text-dim">{label}</span>
        <span className="num">{fmtPct(pct)}</span>
      </div>
      <div className="h-2 rounded-full bg-surface-2">
        <div className="h-2 rounded-full" style={{ width: `${Math.min(pct, 100)}%`, background: tone }} />
      </div>
    </div>
  );

  return (
    <div className="space-y-4">
      <PageHeader title="Capital Allocation" subtitle="mandate targets vs actual sector/position weights" />
      {error && <ErrorState title="API error" detail={error} />}
      {!a ? <SkeletonRows /> : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-12">
          <SectionCard title="Mandate targets" className="rise col-span-12 md:col-span-5">
            <Bar label="Investment sleeve" pct={a.mandate_targets.investment_pct} tone="#34d399" />
            <Bar label="Trading sleeve" pct={a.mandate_targets.trading_pct} tone="#7c9cff" />
            <ul className="mt-3 space-y-1 text-[11px] text-dim">
              <li>max holdings — investment <b className="num text-text">{a.mandate_targets.max_stocks_investment}</b> · trading <b className="num text-text">{a.mandate_targets.max_stocks_trading}</b></li>
              <li>max drawdown <b className="num text-neg">{fmtPct(a.mandate_targets.max_drawdown_pct)}</b> → liquidate-all trigger</li>
            </ul>
          </SectionCard>
          <SectionCard title="Actual sector weights" className="rise col-span-12 md:col-span-7">
            {a.actual.sectors.length === 0 ? (
              <EmptyState title="No positions" hint="Sector weights compute once positions exist." />
            ) : (
              a.actual.sectors.map((s) => (
                <Bar key={s.sector} label={s.sector} pct={s.weight_pct}
                  tone={s.weight_pct > 25 ? "#f87171" : "#f5a623"} />
              ))
            )}
            {a.actual.largest_position_pct != null && (
              <p className="mt-2 text-[11px] text-dim">
                largest single position <b className="num">{fmtPct(a.actual.largest_position_pct)}</b>
              </p>
            )}
            <p className="mt-2 text-[10px] text-faint">{a.note}</p>
          </SectionCard>
        </div>
      )}
    </div>
  );
}
