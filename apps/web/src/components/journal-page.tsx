"use client";

import { useEffect, useState } from "react";
import { History } from "lucide-react";

import { apiGet } from "@/lib/api";
import { fmtNum, fmtTime } from "@/lib/format";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { SkeletonRows } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";

interface Row {
  id: string; symbol: string; at: string; verdict: string;
  confidence: number | null; numbers: Record<string, number>;
  mandate_version: number | null;
}

interface Replay {
  replayed_from: string;
  decision: {
    id: string; symbol: string; at: string; verdict: string;
    confidence: number | null; mandate_version: number | null;
    gate_tree?: { overall: string; gates: { key: string; result: string; reason: string }[] };
    entry_protocol?: { eligible: boolean };
    numbers: Record<string, number>;
    agent_scores: Record<string, number>;
  };
  agent_outputs: { agent: string; status: string; prompt_version: string;
                   report: { recommendation?: string; conclusion?: string } }[];
  approval: { status: string; by: string | null; at: string | null } | null;
  order_tickets: { id: string; side: string; qty: number; limit: number | null;
                   status: string; risk_policy: string; approved_at: string | null }[];
}

const TONE: Record<string, "pos" | "warn" | "neg" | "info"> = {
  approve_pending_human: "pos", approved: "pos", pass: "pos",
  watchlist: "warn", wait: "warn", review: "warn", pending: "info",
  reject: "neg", rejected: "neg", no_trade: "neg", blocked: "neg",
  fail: "neg", rejected_by_human: "neg", proposed: "info",
};

export function JournalPage() {
  const [rows, setRows] = useState<Row[]>([]);
  const [replay, setReplay] = useState<Replay | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiGet<Row[]>("/api/v1/committee/decisions?limit=50")
      .then(setRows).catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const open = async (id: string) => {
    setReplay(await apiGet(`/api/v1/committee/decisions/${id}/replay`));
  };

  return (
    <div className="space-y-4">
      <PageHeader title="Decision Journal" info='Immutable audit trail — every decision, gate result, agent score, approval and order is recorded and replayable.'
        subtitle="immutable decision ledger — replay reconstructs from stored snapshots only" />
      {error && <ErrorState title="API error" detail={error} />}
      {loading && <SkeletonRows />}

      <div className="grid gap-4 lg:grid-cols-2">
        {/* ledger */}
        <SectionCard title={`Decision ledger (${rows.length})`} className="rise">
          {rows.length === 0 ? <EmptyState title="No decisions recorded" /> : (
            <ul className="max-h-[560px] space-y-1.5 overflow-y-auto">
              {rows.map((r) => (
                <li key={r.id}>
                  <button onClick={() => open(r.id)}
                    className="glass-tile flex w-full items-center justify-between px-3 py-2 text-left text-[12px] transition hover:bg-surface-2">
                    <span className="font-semibold text-accent">{r.symbol}</span>
                    <StatusBadge tone={TONE[r.verdict] ?? "info"}>{r.verdict.replace(/_/g, " ")}</StatusBadge>
                    <span className="num text-dim">{r.confidence != null ? `${Math.round(r.confidence * 100)}%` : "—"}</span>
                    <span className="num text-faint">{fmtTime(r.at)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </SectionCard>

        {/* replay */}
        <SectionCard title={replay ? `Replay — ${replay.decision.symbol}` : "Replay"}
          className="rise rise-1"
          action={replay ? <span className="text-[10px] text-faint">{replay.replayed_from}</span> : undefined}>
          {!replay ? (
            <EmptyState title="Select a decision" hint="Full reconstruction: gates, agent outputs, approvals, order states — all from stored snapshots." />
          ) : (
            <div className="space-y-3 text-[12px]">
              <div className="flex items-center gap-2">
                <StatusBadge tone={TONE[replay.decision.verdict] ?? "info"}>{replay.decision.verdict.replace(/_/g, " ")}</StatusBadge>
                <span className="num text-dim">conf {Math.round((replay.decision.confidence ?? 0) * 100)}%</span>
                <span className="text-faint">{fmtTime(replay.decision.at)} · mandate v{replay.decision.mandate_version}</span>
              </div>
              <div className="flex flex-wrap gap-3">
                {Object.entries(replay.decision.numbers).map(([k, v]) => (
                  <span key={k} className="glass-tile px-2.5 py-1 text-[11px]">
                    <span className="text-dim">{k}</span> <span className="num font-medium">{typeof v === "number" ? fmtNum(v, 2) : String(v)}</span>
                  </span>
                ))}
              </div>
              {replay.decision.gate_tree && (
                <div>
                  <div className="mb-1 text-[10px] tracking-wider text-dim uppercase">Gate tree — {replay.decision.gate_tree.overall}</div>
                  <div className="grid grid-cols-2 gap-1">
                    {replay.decision.gate_tree.gates.map((g) => (
                      <div key={g.key} className="flex items-center justify-between rounded-md bg-surface-2 px-2 py-1 text-[11px]">
                        <span className="text-dim">{g.key.replace(/_/g, " ")}</span>
                        <span className={TONE[g.result] === "pos" ? "text-pos" : TONE[g.result] === "neg" ? "text-neg" : TONE[g.result] === "warn" ? "text-warn" : "text-faint"}>{g.result}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div>
                <div className="mb-1 text-[10px] tracking-wider text-dim uppercase">Agent outputs (stored)</div>
                {replay.agent_outputs.map((a, i) => (
                  <div key={i} className="flex items-center justify-between border-b border-border/30 py-1 last:border-0">
                    <span className="text-dim">{a.agent} <span className="text-faint">· {a.prompt_version}</span></span>
                    <StatusBadge tone={TONE[a.report?.recommendation ?? ""] ?? "info"}>{(a.report?.recommendation ?? a.status).replace(/_/g, " ")}</StatusBadge>
                  </div>
                ))}
              </div>
              {replay.approval && (
                <div className="glass-tile px-3 py-2">
                  <span className="text-dim">Human approval:</span>{" "}
                  <StatusBadge tone={TONE[replay.approval.status]}>{replay.approval.status}</StatusBadge>
                  {replay.approval.at && <span className="ml-2 text-faint">{fmtTime(replay.approval.at)}</span>}
                </div>
              )}
              {replay.order_tickets.map((t) => (
                <div key={t.id} className="glass-tile flex items-center justify-between px-3 py-2">
                  <span className="text-dim">order {t.side} <b className="num text-text">{t.qty}</b></span>
                  <StatusBadge tone={TONE[t.status] ?? "info"}>{t.status}</StatusBadge>
                  <span className="text-[10px] text-faint">{t.risk_policy}</span>
                </div>
              ))}
            </div>
          )}
        </SectionCard>
      </div>
    </div>
  );
}
