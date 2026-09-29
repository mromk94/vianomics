"use client";

import { useEffect, useState } from "react";
import { Gavel, Play } from "lucide-react";

import { apiGet, apiPost, ApiError } from "@/lib/api";
import { fmtNum, fmtTime } from "@/lib/format";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { Sym } from "@/components/symbol-drawer";
import { SearchInput } from "@/components/ui/search-input";
import { SectionCard } from "@/components/ui/section-card";
import { SkeletonRows } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";

interface AgentReport {
  agent_key: string; layer: string; ticker: string;
  recommendation: string; score: number; confidence: number;
  data_quality: string; conclusion: string;
  key_evidence: { claim: string; ref_table?: string }[];
  pass_criteria: string[]; fail_criteria: string[];
  key_risks: string[]; contradictions: string[];
  assumptions: string[]; data_gaps: string[]; required_followup: string[];
}

interface Decision {
  decision_id: string;
  verdict: string;
  symbol: string;
  price: number | null;
  tree?: {
    tree_version: string; overall: string;
    gates: { key: string; name: string; result: string; reason: string;
             rules_version: string; evaluated_at: string }[];
  };
  entry_protocol?: {
    eligible: boolean;
    checks: { key: string; ok: boolean; label: string }[];
  };
  cio: {
    score: number; confidence: number; confidence_note: string;
    margin_of_safety: number | null; intrinsic_value: number | null;
    expected_return: number | null; recommendation_horizon: string;
    invalidation_conditions: string[]; disagreements: string[];
    veto_reason: string | null; required_human_approval: boolean;
    report: Record<string, { recommendation: string; score: number; conclusion: string }>;
  };
  agents: Record<string, AgentReport>;
  missing_agents: string[];
}

interface DecisionRow {
  id: string; symbol: string; at: string; verdict: string;
  confidence: number | null; mandate_version: number | null;
}

const LAYER_ORDER = ["research", "research", "research", "research",
                     "research", "research", "control", "capital"];
const AGENT_LABEL: Record<string, string> = {
  fundamental: "Fundamental", valuation: "Valuation",
  rule_one: "Rule #1", quant: "Quant", macro: "Macro",
  technical: "Technical", risk: "Risk Manager", pm: "Portfolio Mgr",
};
const AGENT_ORDER = ["fundamental", "valuation", "rule_one", "quant",
                     "macro", "technical", "risk", "pm"];

const VERDICT_TONE: Record<string, "pos" | "warn" | "neg" | "info"> = {
  approve_pending_human: "pos", approved: "pos", watchlist: "warn",
  reject: "neg", no_trade: "neg", rejected_by_human: "neg",
};
const REC_TONE: Record<string, "pos" | "warn" | "neg" | "info"> = {
  strong_buy: "pos", buy: "pos", pass: "pos", hold: "info",
  wait: "warn", watch: "warn", reduce: "warn", sell: "neg",
  block: "neg", no_trade: "neg", insufficient_data: "info",
};

export function CommitteePage() {
  const [symbol, setSymbol] = useState("NVDA");
  const [decision, setDecision] = useState<Decision | null>(null);
  const [history, setHistory] = useState<DecisionRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  const loadHistory = () =>
    apiGet<DecisionRow[]>("/api/v1/committee/decisions")
      .then(setHistory).catch(() => {});
  useEffect(() => { loadHistory(); }, []);

  const run = async () => {
    setRunning(true); setError(null); setNotice(null);
    try {
      const d = await apiPost<Decision>(`/api/v1/committee/evaluate/${symbol}`, {}, 60000);
      setDecision(d);
      loadHistory();
    } catch (e) {
      setError(e instanceof ApiError && e.status === 401
        ? "Sign in (Settings) to run the committee."
        : (e as Error).message);
    } finally { setRunning(false); }
  };

  const review = async (action: "approve" | "reject") => {
    if (!decision) return;
    try {
      await apiPost(
        `/api/v1/committee/decisions/${decision.decision_id}/review?action=${action}`, {});
      setNotice(`Decision ${action}d — recorded by human reviewer.`);
      loadHistory();
    } catch (e) {
      setNotice(e instanceof ApiError && e.status === 401
        ? "Sign in to review decisions." : (e as Error).message);
    }
  };

  return (
    <div className="space-y-4">
      <PageHeader title="AI Investment Committee" info='Specialist analysts (fundamental, valuation, quant, macro, technical, risk, portfolio) each vote; the CIO synthesizes a verdict. CIO approval is NOT trading permission — a human must still approve every order.'
        subtitle="9 bounded agents → conflict resolution → CIO synthesis → human gate"
        meta={decision ? `${decision.symbol} @ $${fmtNum(decision.price ?? 0, 2)}` : undefined}
        actions={
          <div className="flex items-center gap-2">
            <SearchInput value={symbol} onChange={(v) => setSymbol(v.toUpperCase())} className="w-28" />
            <button onClick={run} disabled={running}
              className="flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-[13px] font-semibold text-[#0b0f1a] transition hover:brightness-110 disabled:opacity-50">
              <Play className="size-3.5" /> {running ? "Running…" : "Evaluate"}
            </button>
          </div>
        } />
      {error && <ErrorState title="Committee error" detail={error} />}
      {notice && <div className="glass border-warn/40 p-3 text-[13px] text-warn">{notice}</div>}

      {decision && (
        <>
          {/* CIO verdict strip */}
          <SectionCard title="CIO Synthesis" className="rise"
            action={<StatusBadge tone={VERDICT_TONE[decision.verdict]}>{decision.verdict.replace(/_/g, " ")}</StatusBadge>}>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-6">
              {[
                ["CIO score", `${decision.cio.score}/100`],
                ["Confidence", `${Math.round(decision.cio.confidence * 100)}%`],
                ["Margin of safety", decision.cio.margin_of_safety != null ? `${(decision.cio.margin_of_safety * 100).toFixed(0)}%` : "—"],
                ["Intrinsic value", decision.cio.intrinsic_value ? `$${fmtNum(decision.cio.intrinsic_value, 2)}` : "—"],
                ["Horizon", decision.cio.recommendation_horizon],
                ["Human approval", decision.cio.required_human_approval ? "required" : "—"],
              ].map(([l, v]) => (
                <div key={l as string} className="glass-tile px-3 py-2">
                  <div className="text-[10px] tracking-wider text-dim uppercase">{l}</div>
                  <div className="num mt-0.5 font-semibold capitalize">{v}</div>
                </div>
              ))}
            </div>
            {decision.cio.veto_reason && (
              <div className="mt-3 rounded-xl border border-neg/40 bg-neg/5 px-3 py-2 text-[13px] text-neg">
                VETO — {decision.cio.veto_reason}
              </div>
            )}
            {decision.cio.disagreements.length > 0 && (
              <div className="mt-2 text-[11px] text-warn">
                disagreements: {decision.cio.disagreements.join(" · ")}
              </div>
            )}
            <div className="mt-1 text-[10px] text-faint">{decision.cio.confidence_note}</div>
            <div className="mt-3 flex gap-2">
              <button onClick={() => review("approve")}
                className="flex items-center gap-1.5 rounded-full border border-pos/40 bg-pos/10 px-4 py-1.5 text-[13px] font-semibold text-pos hover:bg-pos/20">
                <Gavel className="size-3.5" /> Approve
              </button>
              <button onClick={() => review("reject")}
                className="rounded-full border border-neg/40 bg-neg/10 px-4 py-1.5 text-[13px] font-semibold text-neg hover:bg-neg/20">
                Reject
              </button>
            </div>
          </SectionCard>

          {/* Part 22 — gate ladder */}
          {decision.tree && (
            <SectionCard title={`Decision tree — ${decision.tree.overall.replace(/_/g, " ")}`}
              action={decision.tree.tree_version} className="rise">
              <div className="grid gap-1.5 sm:grid-cols-2 xl:grid-cols-4">
                {decision.tree.gates.map((g, i) => (
                  <div key={g.key} className="glass-tile flex items-center justify-between px-3 py-2">
                    <span className="text-[12px]">
                      <span className="mr-1.5 text-faint">{i + 1}.</span>{g.name}
                    </span>
                    <StatusBadge tone={
                      g.result === "pass" || g.result === "approved" ? "pos" :
                      g.result === "pending" ? "info" :
                      g.result === "wait" || g.result === "review" ? "warn" : "neg"
                    }>{g.result.replace(/_/g, " ")}</StatusBadge>
                  </div>
                ))}
              </div>
              {decision.entry_protocol && (
                <div className="mt-3 flex flex-wrap gap-1.5 border-t border-border/40 pt-3">
                  <span className="text-[11px] text-dim">Entry protocol:</span>
                  {decision.entry_protocol.checks.map((c) => (
                    <span key={c.key} title={c.label}
                      className={`rounded-md px-1.5 py-0.5 text-[10px] ${c.ok ? "bg-pos/10 text-pos" : "bg-neg/10 text-neg"}`}>
                      {c.key.replace(/_/g, " ")}
                    </span>
                  ))}
                </div>
              )}
            </SectionCard>
          )}

          {/* agent cards */}
          <div className="rise rise-1 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {AGENT_ORDER.map((k) => {
              const a = decision.agents[k];
              if (!a) return (
                <div key={k} className="glass border-warn/30 p-4 opacity-60">
                  <div className="text-[12px] font-medium">{AGENT_LABEL[k]}</div>
                  <StatusBadge tone="info">missing</StatusBadge>
                </div>);
              return (
                <button key={k} onClick={() => setExpanded(expanded === k ? null : k)}
                  className={`glass p-4 text-left transition hover:bg-surface-2 ${k === "risk" && a.recommendation === "block" ? "border-neg/50" : ""}`}>
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="text-[10px] tracking-wider text-faint uppercase">{a.layer}</div>
                      <div className="text-[13px] font-semibold">{AGENT_LABEL[k]}</div>
                    </div>
                    <StatusBadge tone={REC_TONE[a.recommendation]}>{a.recommendation.replace(/_/g, " ")}</StatusBadge>
                  </div>
                  <div className="num mt-2 text-2xl font-semibold">{a.score.toFixed(0)}</div>
                  <div className="mt-1 line-clamp-2 text-[11px] text-dim">{a.conclusion}</div>
                  <div className="mt-1 text-[10px] text-faint">conf {Math.round(a.confidence * 100)}% · {a.data_quality}</div>
                </button>
              );
            })}
          </div>

          {/* expanded agent detail */}
          {expanded && decision.agents[expanded] && (() => {
            const a = decision.agents[expanded];
            return (
              <SectionCard title={`${AGENT_LABEL[expanded]} — detail`} className="rise">
                <div className="grid gap-4 lg:grid-cols-3">
                  <div>
                    <div className="mb-1 text-[10px] tracking-wider text-dim uppercase">Evidence</div>
                    {a.key_evidence.map((e, i) => (
                      <div key={i} className="mb-1 text-[12px]">
                        {e.claim}
                        {e.ref_table && <span className="ml-1 text-[10px] text-faint">[{e.ref_table}]</span>}
                      </div>
                    ))}
                    {!a.key_evidence.length && <span className="text-[12px] text-faint">none</span>}
                  </div>
                  <div>
                    <div className="mb-1 text-[10px] tracking-wider text-dim uppercase">Risks / Gaps</div>
                    {[...a.key_risks, ...a.data_gaps.map((g) => `gap: ${g}`)].map((r, i) => (
                      <div key={i} className="text-[12px] text-dim">· {r}</div>
                    ))}
                    {a.assumptions.map((r, i) => (
                      <div key={i} className="text-[11px] text-faint">assumed: {r}</div>
                    ))}
                  </div>
                  <div>
                    <div className="mb-1 text-[10px] tracking-wider text-dim uppercase">Criteria</div>
                    {a.pass_criteria.map((c, i) => <div key={i} className="text-[12px] text-pos">✓ {c}</div>)}
                    {a.fail_criteria.map((c, i) => <div key={i} className="text-[12px] text-neg">✗ {c}</div>)}
                  </div>
                </div>
              </SectionCard>
            );
          })()}

          {decision.missing_agents.length > 0 && (
            <div className="glass border-warn/40 p-3 text-[12px] text-warn">
              Missing agent output: {decision.missing_agents.join(", ")} — treated as unavailable, never as pass.
            </div>
          )}
        </>
      )}

      {/* history */}
      <SectionCard title="Decision history" className="rise rise-2">
        {history.length === 0 ? (
          <EmptyState title="No decisions yet" hint="Run an evaluation to populate the ledger." />
        ) : (
          <ul className="space-y-1.5">
            {history.map((h) => (
              <li key={h.id} className="glass-tile flex items-center justify-between px-3 py-2 text-[12px]">
                <Sym s={h.symbol} />
                <StatusBadge tone={VERDICT_TONE[h.verdict] ?? "info"}>{h.verdict.replace(/_/g, " ")}</StatusBadge>
                <span className="num text-dim">conf {h.confidence != null ? Math.round(h.confidence * 100) : "—"}%</span>
                <span className="num text-faint">{fmtTime(h.at)}</span>
                <span className="text-[10px] text-faint">mandate v{h.mandate_version}</span>
              </li>
            ))}
          </ul>
        )}
      </SectionCard>

      {!decision && !running && (
        <EmptyState title="Run the committee" hint="All 9 agents evaluate; Risk Manager veto is absolute; human approval required." />
      )}
    </div>
  );
}
