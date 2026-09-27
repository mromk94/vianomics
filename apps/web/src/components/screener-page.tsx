"use client";

import { useEffect, useMemo, useState } from "react";
import { Play } from "lucide-react";

import { apiGet, apiPost, ApiError, getToken } from "@/lib/api";
import { fmtDate, fmtTime } from "@/lib/format";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Drawer } from "@/components/ui/drawer";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { FilterSelect } from "@/components/ui/filter-select";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { StatusBadge } from "@/components/ui/status-badge";

type Verdict = "pass" | "fail" | "review" | "insufficient_data" | "blocked_by_risk";

interface Row {
  symbol: string;
  name: string;
  asset_class: string;
  listing_status: string;
  score: number;
  applicable: number;
  verdict: Verdict;
  qualified: boolean;
  blocked_reasons: string[];
}

interface Criterion {
  key: string;
  name: string;
  status: string;
  score: number;
  formula: string;
  evidence: Record<string, unknown>;
  industry_variant: boolean;
  review_required: boolean;
}

interface Detail extends Row {
  criteria: Criterion[];
  policy_version: number;
  mandate_version: number | null;
  as_of: string;
  data_freshness: string | null;
}

const VERDICT_TONE: Record<Verdict, "pos" | "neg" | "warn" | "info"> = {
  pass: "pos",
  fail: "neg",
  review: "warn",
  insufficient_data: "info",
  blocked_by_risk: "neg",
};

const VERDICT_LABEL: Record<Verdict, string> = {
  pass: "PASS",
  fail: "FAIL",
  review: "REVIEW",
  insufficient_data: "INSUFFICIENT DATA",
  blocked_by_risk: "BLOCKED BY RISK",
};

const CRIT_TONE: Record<string, "pos" | "neg" | "warn" | "info" | "neutral"> = {
  pass: "pos",
  fail: "neg",
  review: "warn",
  insufficient_data: "info",
  not_applicable: "neutral",
};

export function ScreenerPage() {
  const [data, setData] = useState<{ run: { id: string; policy_version: number; mandate_version: number | null; started_at: string; universe: string } | null; results: Row[] } | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [verdict, setVerdict] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);

  const load = () => {
    apiGet<{ run: never; results: Row[] }>("/api/v1/screener/latest")
      .then((d) => setData(d as never))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const runScreen = async () => {
    setRunning(true);
    try {
      await apiPost("/api/v1/screener/run", {});
      setNotice(null);
      load();
    } catch (e) {
      setNotice(
        e instanceof ApiError && e.status === 401
          ? "Sign in (Settings) with a research role to run the screen."
          : (e as Error).message,
      );
    } finally {
      setRunning(false);
    }
  };

  const rows = useMemo(
    () => (data?.results ?? []).filter((r) => !verdict || r.verdict === verdict),
    [data, verdict],
  );

  const columns: Column<Row>[] = [
    {
      key: "symbol", header: "Ticker",
      render: (r) => (
        <button onClick={() => apiGet<Detail>(`/api/v1/screener/results/${r.symbol}`).then(setDetail)}
          className="font-semibold text-accent hover:underline">
          {r.symbol}
        </button>
      ),
    },
    { key: "name", header: "Name", render: (r) => <span className="text-dim">{r.name}</span> },
    {
      key: "score", header: "Score", align: "right",
      render: (r) => (
        <span className="num font-semibold">{r.score}/{r.applicable}</span>
      ),
    },
    {
      key: "verdict", header: "Status",
      render: (r) => <StatusBadge tone={VERDICT_TONE[r.verdict]} dot>{VERDICT_LABEL[r.verdict]}</StatusBadge>,
    },
    {
      key: "qual", header: "Qualified",
      render: (r) =>
        r.qualified ? <StatusBadge tone="pos">yes</StatusBadge> : <span className="text-faint">—</span>,
    },
    {
      key: "blocked", header: "Blocks",
      render: (r) =>
        r.blocked_reasons.length ? (
          <span className="text-[12px] text-neg">{r.blocked_reasons.join(", ")}</span>
        ) : <span className="text-faint">—</span>,
    },
  ];

  return (
    <div className="space-y-4">
      <PageHeader info='Green Zone screen: 20 criteria scored 0–20. A passing score means a candidate is worth deeper research — not that it should be bought.'
        title="Green Zone Screener"
        subtitle="20-criteria fundamental screen — deterministic, evidence-backed"
        actions={
          <button
            onClick={runScreen}
            disabled={running}
            className="flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-[13px] font-semibold text-[#0b0f1a] transition enabled:hover:brightness-110 disabled:opacity-50"
          >
            <Play className="size-3.5" /> {running ? "Running…" : "Run screen"}
          </button>
        }
        meta={
          data?.run
            ? `run ${fmtTime(data.run.started_at)} · policy v${data.run.policy_version} · mandate v${data.run.mandate_version ?? "—"} · ${data.run.universe}`
            : "no screen run yet"
        }
      />
      {notice && <div className="glass border-warn/40 p-3 text-[13px] text-warn">{notice}</div>}
      {error && <ErrorState title="Cannot reach the VAIIP API" detail={error} onRetry={load} />}

      <SectionCard title="Screening results" className="rise"
        action={
          <FilterSelect
            value={verdict}
            onChange={setVerdict}
            options={[
              { value: "", label: "All statuses" },
              { value: "pass", label: "PASS" },
              { value: "review", label: "REVIEW" },
              { value: "fail", label: "FAIL" },
              { value: "insufficient_data", label: "INSUFFICIENT DATA" },
              { value: "blocked_by_risk", label: "BLOCKED BY RISK" },
            ]}
          />
        }>
        <DataTable
          columns={columns}
          rows={rows}
          loading={loading}
          rowKey={(r) => r.symbol}
          empty="No screening run yet — click Run screen"
        />
      </SectionCard>

      <Drawer open={!!detail} onClose={() => setDetail(null)}
        title={detail ? `${detail.symbol} — ${detail.score}/${detail.applicable} ${VERDICT_LABEL[detail.verdict]}` : ""}>
        {detail && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2 text-[12px] text-faint">
              <StatusBadge tone={VERDICT_TONE[detail.verdict]} dot>
                {VERDICT_LABEL[detail.verdict]}
              </StatusBadge>
              <span>policy v{detail.policy_version}</span>
              <span>mandate v{detail.mandate_version ?? "—"}</span>
              <span>as of {fmtTime(detail.as_of)}</span>
              {detail.data_freshness && <span>data to {fmtDate(detail.data_freshness)}</span>}
            </div>
            <ul className="space-y-1.5">
              {detail.criteria.map((c) => (
                <li key={c.key} className="glass-tile px-3 py-2">
                  <div className="flex items-center justify-between">
                    <span className="text-[13px] font-medium">
                      {c.name}
                      {c.industry_variant && (
                        <span className="ml-1.5 text-[10px] text-faint">(sector variant)</span>
                      )}
                      {c.review_required && (
                        <span className="ml-1.5 text-[10px] text-warn">needs review</span>
                      )}
                    </span>
                    <span className="flex items-center gap-2">
                      <span className="num text-[12px] text-dim">{c.score}</span>
                      <StatusBadge tone={CRIT_TONE[c.status] ?? "neutral"}>
                        {c.status.replace(/_/g, " ")}
                      </StatusBadge>
                    </span>
                  </div>
                  <div className="mt-0.5 text-[11px] text-faint">{c.formula}</div>
                  {Object.keys(c.evidence).length > 0 && (
                    <div className="num mt-1 text-[11px] text-dim">
                      {Object.entries(c.evidence)
                        .map(([k, v]) => `${k}: ${typeof v === "number" ? Math.abs(v) < 10 ? v.toFixed(3) : v.toLocaleString() : Array.isArray(v) ? v.join(", ") : v}`)
                        .join("  ·  ")}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </Drawer>
    </div>
  );
}
