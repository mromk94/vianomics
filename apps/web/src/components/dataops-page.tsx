"use client";

import { useEffect, useState } from "react";

import { apiGet, apiPost } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import { DataTable } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { MetricCard } from "@/components/ui/metric-card";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { SkeletonRows } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";

interface Overview {
  jobs: { id: string; key: string; kind: string; enabled: boolean;
    provider: string | null; last_run_at: string | null;
    last_status: string | null }[];
  sync: { provider: string; dataset: string; last_success: string | null;
    last_error: string | null; lag_seconds: number | null }[];
  quarantine: { target_table: string; count: number }[];
}

const ST: Record<string, "pos" | "neg" | "warn"> = {
  success: "pos", failed: "neg", partial: "warn", running: "warn",
};

export function DataOpsPage() {
  const [d, setD] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState<string | null>(null);
  const load = () => apiGet<Overview>("/api/v1/dataops/overview")
    .then(setD).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);
  const runNow = async (key: string) => {
    setRunning(key);
    try { await apiPost(`/api/v1/dataops/run/${key}`, {}); await load(); }
    finally { setRunning(null); }
  };

  const failed = d?.jobs.filter((j) => j.last_status === "failed").length ?? 0;
  const quar = d?.quarantine.reduce((s, q) => s + q.count, 0) ?? 0;

  return (
    <div className="space-y-4">
      <PageHeader title="Data Operations" info='Health of every data feed: what ran, when it last succeeded, and records that failed validation. Quarantined data is kept for review — never silently dropped.'
        subtitle="ingestion jobs, provider sync freshness, quarantine — failures surface as alerts, not silence" />
      {error && <ErrorState title="API error" detail={error} />}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricCard label="Registered jobs" value={d ? String(d.jobs.length) : "—"} />
        <MetricCard label="Failed last runs" value={d ? String(failed) : "—"} tone={failed ? "neg" : "pos"} />
        <MetricCard label="Quarantined records" value={d ? String(quar) : "—"} tone={quar ? "warn" : "pos"} />
        <MetricCard label="Sync streams" value={d ? String(d.sync.length) : "—"} />
      </div>

      <SectionCard title="Jobs" className="rise">
        {!d ? <SkeletonRows /> : (
          <DataTable
            columns={[
              { key: "k", header: "Job", render: (j: Overview["jobs"][0]) => <span className="num text-[11px]">{j.key}</span> },
              { key: "p", header: "Provider", render: (j) => <span className="text-dim">{j.provider ?? "—"}</span> },
              { key: "st", header: "Last run", render: (j) => j.last_status
                ? <StatusBadge tone={ST[j.last_status] ?? "warn"}>{j.last_status}</StatusBadge>
                : <span className="text-faint text-[11px]">never</span> },
              { key: "at", header: "At", align: "right", render: (j) => <span className="num text-faint text-[11px]">{j.last_run_at ? fmtTime(j.last_run_at) : "—"}</span> },
              { key: "run", header: "", render: (j) => (
                <button onClick={() => runNow(j.key)} disabled={running === j.key}
                  className="text-[10px] text-accent hover:underline disabled:opacity-40">
                  {running === j.key ? "running…" : "run"}</button>) },
            ]}
            rows={d.jobs} rowKey={(j) => j.id}
          />
        )}
      </SectionCard>

      <div className="grid grid-cols-12 gap-4">
        <SectionCard title="Provider sync freshness" className="rise col-span-7">
          {!d ? <SkeletonRows /> : d.sync.length === 0 ? (
            <EmptyState title="No sync status" hint="Provider syncs write here." />
          ) : (
            <DataTable
              columns={[
                { key: "p", header: "Provider", render: (s: Overview["sync"][0]) => <span className="font-semibold">{s.provider}</span> },
                { key: "d", header: "Dataset", render: (s) => <span className="text-dim text-[11px]">{s.dataset}</span> },
                { key: "t", header: "Last success", align: "right", render: (s) => <span className="num text-[11px] text-faint">{s.last_success ? fmtTime(s.last_success) : "—"}</span> },
                { key: "e", header: "Error", render: (s) => s.last_error ? <span className="text-neg text-[11px]">{s.last_error.slice(0, 40)}</span> : <span className="text-faint">—</span> },
              ]}
              rows={d.sync} rowKey={(s) => s.provider + s.dataset}
            />
          )}
        </SectionCard>
        <SectionCard title="Quarantine" className="rise col-span-5">
          {!d ? <SkeletonRows /> : d.quarantine.length === 0 ? (
            <EmptyState title="Clean" hint="Invalid records would land here — never silently dropped." />
          ) : (
            <ul className="space-y-1 text-[12px]">
              {d.quarantine.map((q) => (
                <li key={q.target_table} className="flex justify-between border-b border-border/40 py-1">
                  <span className="text-dim">{q.target_table}</span>
                  <b className="num text-warn">{q.count}</b>
                </li>
              ))}
            </ul>
          )}
        </SectionCard>
      </div>
    </div>
  );
}
