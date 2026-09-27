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

export function MarketPage() {
  const [rows, setRows] = useState<Snap[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    apiGet<Snap[]>("/api/v1/market/snapshot").then(setRows)
      .catch((e) => setError(e.message));
  }, []);

  return (
    <div className="space-y-4">
      <PageHeader title="Market Overview" info='Latest daily price for every tracked instrument. Prices are end-of-day from your market-data provider — not a live tape.'
        subtitle="latest daily bars per instrument — delayed EOD data, not a live tape"
        meta={rows ? `${rows.length} instruments` : ""} />
      {error && <ErrorState title="API error" detail={error} />}
      <SectionCard className="rise">
        {!rows ? <SkeletonRows /> : rows.length === 0 ? (
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
        )}
      </SectionCard>
    </div>
  );
}
