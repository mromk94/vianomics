"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { apiGet } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { DataTable } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { SkeletonRows } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";

interface Row {
  symbol: string; decision: string; mr: string; tf: string;
  rsi_1d: number | null; aroon_up: number | null;
  last_close: number | null; fresh: boolean;
}

const TONE: Record<string, "pos" | "neg" | "warn" | "info" | "neutral"> = {
  entry_signal: "pos", watch_dip: "info", avoid: "neg", wait: "warn",
};

export function TechnicalPage() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    apiGet<Row[]>("/api/v1/technical/scan").then(setRows)
      .catch((e) => setError(e.message));
  }, []);

  const signals = rows?.filter((r) => r.decision === "entry_signal") ?? [];

  return (
    <div className="space-y-4">
      <PageHeader title="Technical Engine"
        subtitle="per-symbol regime + MR/TF signals — chart view on Trading Desk"
        meta={rows ? `${signals.length} entry signals` : ""} />
      {error && <ErrorState title="API error" detail={error} />}
      <SectionCard className="rise">
        {!rows ? <SkeletonRows /> : rows.length === 0 ? (
          <EmptyState title="No scan data" hint="Instruments need daily bars." />
        ) : (
          <DataTable
            columns={[
              { key: "s", header: "Symbol", render: (r: Row) => (
                <Link href={`/trading-desk?sym=${r.symbol}`} className="font-semibold text-accent hover:underline">{r.symbol}</Link>) },
              { key: "d", header: "Decision", render: (r) => <StatusBadge tone={TONE[r.decision] ?? "neutral"}>{r.decision}</StatusBadge> },
              { key: "mr", header: "MR", render: (r) => <span className="text-[11px] text-dim">{r.mr}</span> },
              { key: "tf", header: "TF", render: (r) => <span className="text-[11px] text-dim">{r.tf}</span> },
              { key: "rsi", header: "RSI 1d", align: "right", render: (r) => <span className="num">{r.rsi_1d != null ? fmtNum(r.rsi_1d, 0) : "—"}</span> },
              { key: "ar", header: "Aroon ↑", align: "right", render: (r) => <span className="num">{r.aroon_up != null ? fmtNum(r.aroon_up, 0) : "—"}</span> },
              { key: "c", header: "Close", align: "right", render: (r) => <span className="num">{r.last_close ? `$${fmtNum(r.last_close, 2)}` : "—"}</span> },
              { key: "f", header: "Data", render: (r) => <StatusBadge tone={r.fresh ? "pos" : "warn"}>{r.fresh ? "fresh" : "stale"}</StatusBadge> },
            ]}
            rows={rows} rowKey={(r) => r.symbol}
          />
        )}
      </SectionCard>
    </div>
  );
}
