"use client";

import { useEffect, useState } from "react";
import { BellRing, Play } from "lucide-react";

import { apiGet, apiPost, ApiError } from "@/lib/api";
import { fmtNum, fmtTime } from "@/lib/format";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { SkeletonRows } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";

interface Alert {
  id: string; severity: string; source: string; message: string;
  status: string; created_at: string; resolved_at: string | null;
  context: {
    dedup_key?: string; observed?: number | string;
    required?: number | string; action?: string; data_fresh?: boolean;
  };
}

const SEV_TONE: Record<string, "neg" | "warn" | "info"> = {
  critical: "neg", warning: "warn", info: "info",
};
const SRC_LABEL: Record<string, string> = {
  "monitor:macro": "Macro", "monitor:trading": "Trading",
  "monitor:portfolio": "Portfolio", "monitor:valuation": "Valuation",
  "monitor:data": "Data", "monitor:fundamental": "Fundamental",
};

export function MonitoringPage() {
  const [list, setList] = useState<Alert[]>([]);
  const [filter, setFilter] = useState("active");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);

  const load = () => {
    apiGet<Alert[]>(`/api/v1/monitoring/alerts?status=${filter}&limit=100`)
      .then(setList).catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };
  useEffect(load, [filter]);

  const act = async (id: string, op: "ack" | "resolve") => {
    try {
      await apiPost(`/api/v1/monitoring/alerts/${id}/${op}`, {});
      load();
    } catch (e) {
      setNotice(e instanceof ApiError && e.status === 401
        ? "Sign in (Settings) to manage alerts." : (e as Error).message);
    }
  };

  const runScan = async () => {
    setScanning(true); setNotice(null);
    try {
      const r = await apiPost<{ emitted: Record<string, number> }>(
        "/api/v1/monitoring/scan", {});
      const total = Object.values(r.emitted).reduce((a, b) => a + b, 0);
      setNotice(total
        ? `Scan emitted ${total} alert(s).`
        : "Scan complete — no new alerts (dedupe may suppress repeats).");
      load();
    } catch (e) {
      setNotice(e instanceof ApiError && e.status === 401
        ? "Sign in to run scans." : (e as Error).message);
    } finally { setScanning(false); }
  };

  const active = list.filter((a) => a.status === "active");
  const crits = active.filter((a) => a.severity === "critical");

  return (
    <div className="space-y-4">
      <PageHeader title="Monitoring & Alerts"
        subtitle="thesis, market, portfolio, order and data-quality monitors — deduped, never faked"
        meta={`${active.length} active · ${crits.length} critical`}
        actions={
          <button onClick={runScan} disabled={scanning}
            className="flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-[13px] font-semibold text-[#0b0f1a] hover:brightness-110 disabled:opacity-50">
            <Play className="size-3.5" /> {scanning ? "Scanning…" : "Run scan"}
          </button>
        } />
      {notice && <div className="glass border-warn/40 p-3 text-[13px] text-warn">{notice}</div>}
      {error && <ErrorState title="API error" detail={error} onRetry={load} />}
      {loading && <SkeletonRows />}

      <SectionCard title="Alerts" className="rise"
        action={
          <div className="flex gap-1">
            {["active", "acknowledged", "resolved", "all"].map((f) => (
              <button key={f} onClick={() => setFilter(f)}
                className={`rounded-full px-2.5 py-1 text-[11px] capitalize transition ${filter === f ? "bg-accent text-[#0b0f1a] font-semibold" : "border border-border text-dim"}`}>{f}</button>
            ))}
          </div>
        }>
        {list.length === 0 ? (
          <EmptyState title="No alerts" hint="Run a scan — failed providers surface as data-quality alerts, never as silence." />
        ) : (
          <ul className="space-y-1.5">
            {list.map((a) => (
              <li key={a.id} className="glass-tile px-3 py-2.5">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <StatusBadge tone={SEV_TONE[a.severity]}>{a.severity}</StatusBadge>
                      <span className="text-[10px] text-faint">{SRC_LABEL[a.source] ?? a.source}</span>
                      {a.context.data_fresh === false && (
                        <StatusBadge tone="warn">stale data</StatusBadge>)}
                    </div>
                    <div className="mt-1 text-[13px]">{a.message}</div>
                    <div className="mt-0.5 flex flex-wrap gap-x-3 text-[11px] text-dim">
                      {a.context.observed != null && <span>observed <b className="num text-text">{String(a.context.observed)}</b></span>}
                      {a.context.required != null && <span>required <b className="num">{String(a.context.required)}</b></span>}
                      {a.context.action && <span>→ {a.context.action}</span>}
                    </div>
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1.5">
                    <span className="num text-[10px] text-faint">{fmtTime(a.created_at)}</span>
                    {a.status === "active" && (
                      <div className="flex gap-1">
                        <button onClick={() => act(a.id, "ack")}
                          className="rounded-md border border-border px-2 py-0.5 text-[10px] text-dim hover:text-text">ack</button>
                        <button onClick={() => act(a.id, "resolve")}
                          className="rounded-md border border-pos/40 px-2 py-0.5 text-[10px] text-pos hover:bg-pos/10">resolve</button>
                      </div>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </SectionCard>
    </div>
  );
}
