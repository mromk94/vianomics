"use client";

import { useEffect, useMemo, useState } from "react";
import { Play } from "lucide-react";

import { apiGet, apiPost, ApiError } from "@/lib/api";
import { fmtDate, fmtNum, fmtTime } from "@/lib/format";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Drawer } from "@/components/ui/drawer";
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
  counts?: Record<string, number>;
  failed?: string[];
  review_items?: string[];
  missing?: string[];
  /** "strong" (≥90% of applicable) | "conditional" (15–17 band) | "below" */
  band?: string;
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
  sector: string | null;
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

const VERDICT_MEANING: Record<Verdict, string> = {
  pass: "Cleared the bar — qualifies for deeper research. A pass is a research green light, not a buy order.",
  review: "Cleared the floor but not the full bar — either a 15–17 conditional band (doc: watchlist pending review) or a criterion awaiting human confirmation.",
  fail: "Did not clear the pass bar — see the failed criteria below for exactly why.",
  insufficient_data: "Not enough reported data to judge half the criteria — ingest EDGAR facts before trusting any verdict.",
  blocked_by_risk: "Excluded by risk regardless of fundamentals — the listing itself is not tradable.",
};

const CRIT_TONE: Record<string, "pos" | "neg" | "warn" | "info" | "neutral"> = {
  pass: "pos",
  fail: "neg",
  review: "warn",
  insufficient_data: "info",
  not_applicable: "neutral",
};

const CRIT_STATUS_LABEL: Record<string, string> = {
  pass: "PASS",
  fail: "FAIL",
  review: "REVIEW",
  insufficient_data: "NO DATA",
  not_applicable: "N/A",
};

/* Criteria grouped the way a trader reads a quality screen —
   business momentum → cash & capital quality → leverage → price. */
const GROUPS: { title: string; keys: string[] }[] = [
  {
    title: "Growth & profitability",
    keys: ["revenue_growth", "net_income_growth", "ocf_growth",
           "margin_expansion", "roe", "roic", "revenue_receivables"],
  },
  {
    title: "Cash & capital quality",
    keys: ["cash_conversion", "share_count", "fcf_per_share",
           "bvps", "dividend_growth", "moat"],
  },
  {
    title: "Balance sheet & leverage",
    keys: ["current_ratio", "debt_ebitda", "interest_coverage", "dscr"],
  },
  {
    title: "Valuation & technical",
    keys: ["p_fcf", "iv_discount", "technical_setup",
           "macro_sector_alignment", "relative_strength", "liquidity"],
  },
];

/* ── formatting helpers — units a trader actually reads ── */

const usd = (v: unknown): string => {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  const a = Math.abs(n);
  if (a >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `$${(n / 1e3).toFixed(1)}K`;
  return `$${n.toFixed(0)}`;
};
const pct = (v: unknown, digits = 1): string =>
  Number.isFinite(Number(v)) ? `${(Number(v) * 100).toFixed(digits)}%` : "—";
const sgn = (v: number) => (v >= 0 ? "+" : "");
const px = (v: unknown) =>
  Number.isFinite(Number(v)) ? `$${Number(v).toFixed(2)}` : "—";
const xx = (v: unknown) =>
  Number.isFinite(Number(v)) ? `${Number(v).toFixed(2)}×` : "—";

const growthLine = (e: Record<string, unknown>, label: string, unit = "%") =>
  `${label} ${sgn(Number(e.growth))}${pct(e.growth)} YoY — ` +
  `${usd(e.prev)} → ${usd(e.curr)} (bar: ≥ 0%)`;

/* One plain-English sentence per criterion: what was measured, the
   number, the bar, and why it passed/failed. */
function explain(c: Criterion): string {
  const e = c.evidence || {};
  if (c.status === "insufficient_data") {
    const missing = Array.isArray(e.missing) ? e.missing.join(", ") : "required history";
    return `Cannot judge — missing ${missing}. ` +
      `Run ingest:edgar:facts (fundamentals) or the bars backfill before this counts.`;
  }
  if (c.status === "not_applicable") {
    return typeof e.note === "string" ? e.note
      : `Not applicable${e.sector ? ` for ${e.sector}` : " in this case"} — excluded from the score.`;
  }
  switch (c.key) {
    case "revenue_growth": return growthLine(e, "Revenue");
    case "net_income_growth": return growthLine(e, "Net income");
    case "ocf_growth": return growthLine(e, "Operating cash flow");
    case "margin_expansion":
      return `Operating margin ${pct(e.margin_curr)} vs ${pct(e.margin_prev)} ` +
        `(${sgn(Number(e.delta_pp))}${Number(e.delta_pp).toFixed(1)}pp) — needs ≥ 0pp expansion.`;
    case "roe":
      return `ROE ${pct(e.roe)} — net income on ${usd(e.equity)} equity (bar ≥ 15%).`;
    case "roic":
      return `ROIC ${pct(e.roic)} — after-tax operating profit on invested capital (bar ≥ 12%).`;
    case "revenue_receivables":
      return `Revenue ${sgn(Number(e.rev_growth))}${pct(e.rev_growth)} vs receivables ` +
        `${sgn(Number(e.ar_growth))}${pct(e.ar_growth)} — sales not outrunning uncollected invoices.`;
    case "cash_conversion":
      return `${usd(e.ocf)} OCF vs ${usd(e.ni)} net income → ${xx(e.ratio)} ` +
        `conversion (bar ≥ 0.90× — earnings backed by cash).`;
    case "share_count":
      return `Share count ${sgn(Number(e.change))}${pct(e.change)} YoY — ` +
        (Number(e.change) <= 0 ? "buybacks shrinking the float."
          : Number(e.change) <= 0.02 ? "flat — no meaningful dilution."
          : "dilution above the +2% tolerance.");
    case "fcf_per_share":
      return `FCF ${usd(e.fcf)} → ${px(e.fcf_ps)}/share — free cash after capex (bar: > $0).`;
    case "p_fcf":
      return `P/FCF ${Number(e.p_fcf).toFixed(1)}× — price per dollar of free cash (cap 30×).`;
    case "bvps": {
      const vals = Array.isArray(e.bvps) ? e.bvps.map((v) => px(v)).join(" → ") : "—";
      return `Book value per share ${vals} — positive and not declining.`;
    }
    case "dividend_growth":
      return `DPS ${usd(e.prev)} → ${usd(e.curr)} — dividend growing YoY.`;
    case "moat": {
      const r = Array.isArray(e.roic_history)
        ? e.roic_history.map((v) => pct(v)).join(", ") : "—";
      const m = Array.isArray(e.margin_history)
        ? e.margin_history.map((v) => pct(v)).join(", ") : "—";
      return `ROIC 3y [${r}] ≥ 12% + EBIT margin 3y [${m}] ≥ 15% — ` +
        `deterministic evidence only; a moat always needs human sign-off.`;
    }
    case "current_ratio":
      return `Current ratio ${xx(e.current_ratio)} — short-term assets vs liabilities (bar ≥ 1.5×).`;
    case "debt_ebitda":
      return `Debt/EBITDA ${xx(e.debt_ebitda)} — years of operating profit to repay debt (cap ≤ 3.0×).`;
    case "interest_coverage":
      return `EBIT covers interest ${xx(e.interest_coverage)} (bar ≥ 5×) — ` +
        (Number(e.interest_coverage) >= 5 ? "debt load is serviceable."
          : "earnings thin against interest costs.");
    case "dscr":
      return `DSCR ${xx(e.dscr)} — operating cash flow vs debt service due (bar ≥ 1.5×).`;
    case "iv_discount":
      return `Price ${px(e.price)} vs intrinsic value ${px(e.iv)} → ${pct(e.discount)} ` +
        `discount (bar ≥ 20% margin of safety).`;
    case "technical_setup":
      return `Close ${px(e.close)} vs SMA200 ${px(e.sma)} — price ` +
        (Number(e.close) > Number(e.sma)
          ? "above the 200-day line: the trend is on your side."
          : "below the 200-day line: trading against the trend.");
    case "macro_sector_alignment":
      return `${e.sector} ${e.favored ? "is favored" : "is not favored"} under the ` +
        `${e.econ_regime ?? "current"} regime — the fund's macro thesis channel.`;
    case "relative_strength":
      return `63-day return ${sgn(Number(e.etf_r63))}${pct(e.etf_r63)} vs SPY ` +
        `${sgn(Number(e.spy_r63))}${pct(e.spy_r63)} — spread ${sgn(Number(e.spread))}${pct(e.spread)}.`;
    case "liquidity":
      return `30-day dollar volume ${usd(e.adv30)} — ` +
        (Number(e.adv30) >= 5e6 ? "clears the $5M tradability floor."
          : "below the $5M floor — too thin to trade safely.");
    default:
      return "";
  }
}

/* Evidence chips — every recorded input, formatted with units. */
function evidenceChips(c: Criterion): { k: string; v: string }[] {
  const fmt = (k: string, v: unknown): string => {
    if (v == null) return "—";
    if (typeof v === "boolean") return v ? "yes" : "no";
    if (Array.isArray(v)) return v.map((x) => fmt(k, x)).join(", ");
    if (typeof v === "object") return JSON.stringify(v);
    const n = Number(v);
    if (!Number.isFinite(n)) return String(v);
    if (/_pct$|growth$|^growth|^delta_pp|^roe$|^roic$|margin|change|ratio$|^spread|r63|discount/.test(k))
      return k === "ratio" || k === "current_ratio" ? `${n.toFixed(2)}×` : pct(n);
    if (/price|^iv$|^close$|^sma$|^fcf_ps$|bvps/.test(k)) return px(n);
    if (/rev|ni$|ocf|equity|debt|cash|fcf$|adv30|interest|prev|curr|shares|market_value|capex|dps/.test(k))
      return Math.abs(n) >= 1e5 ? usd(n) : fmtNum(n, 2);
    return Math.abs(n) < 10 ? n.toFixed(3) : n.toLocaleString();
  };
  return Object.entries(c.evidence || {})
    .filter(([k]) => k !== "missing")
    .map(([k, v]) => ({ k: k.replace(/_/g, " "), v: fmt(k, v) }));
}

function CountChip({ n, label, tone }: { n: number; label: string; tone: string }) {
  if (!n) return null;
  return (
    <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${tone}`}>
      {n} {label}
    </span>
  );
}

export function ScreenerPage() {
  const [data, setData] = useState<{ run: { id: string; policy_version: number; mandate_version: number | null; started_at: string; universe: string } | null; results: Row[] } | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [verdict, setVerdict] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  /* scope — the doc's universe is a ladder, not a wall: any tier or
     an explicit ticker list can run through the same 20 criteria */
  const [scope, setScope] = useState("approved");
  const [customSyms, setCustomSyms] = useState("");

  const openDetail = (sym: string) =>
    apiGet<Detail>(`/api/v1/screener/results/${sym}`).then(setDetail);

  /* Human sign-off on a REVIEW criterion (e.g. moat evidence) —
     clears it to pass and recomputes the verdict server-side. */
  const confirmCriterion = async (criterion: string) => {
    if (!detail) return;
    setConfirming(criterion);
    try {
      await apiPost(`/api/v1/screener/results/${detail.symbol}/confirm`,
                    { criterion });
      await openDetail(detail.symbol);
      load();
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setConfirming(null);
    }
  };

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
      const body = scope === "custom"
        ? { symbols: customSyms.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean) }
        : { universe: scope };
      if (scope === "custom" && !body.symbols?.length) {
        setNotice("Enter at least one ticker for a custom screen.");
        setRunning(false);
        return;
      }
      // the screen runs server-side in the background — broad
      // universes are hundreds of instruments × ~30 queries each,
      // far beyond any request window. Poll the job until it lands.
      const res = await apiPost<{ status: string; job_run_id: string }>(
        "/api/v1/screener/run", body);
      if (res.status !== "running" || !res.job_run_id) {
        setNotice("Unexpected run response — check /monitoring.");
        setRunning(false);
        return;
      }
      setNotice("Screening in the background…");
      const deadline = Date.now() + 5 * 60_000;
      for (;;) {
        await new Promise((r) => setTimeout(r, 2500));
        const j = await apiGet<{ status: string; instruments?: number;
          note?: string | null }>(`/api/v1/screener/jobs/${res.job_run_id}`);
        if (j.status === "running") {
          if (Date.now() > deadline) {
            setNotice("Still running — it will land in /monitoring when done.");
            break;
          }
          continue;
        }
        setNotice([
          j.note,
          j.instruments != null ? `${j.instruments} screened` : null,
          j.status === "failed" ? "run failed" : null,
        ].filter(Boolean).join(" · ") ||
          (j.status === "success" ? "complete" : j.status));
        if (j.status === "success") load();
        break;
      }
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
        <span className="font-semibold text-accent">{r.symbol}</span>
      ),
    },
    { key: "name", header: "Name", render: (r) => <span className="text-dim">{r.name}</span> },
    {
      key: "score", header: "Score", align: "right",
      render: (r) => {
        const pc = r.applicable ? Math.round(r.score / r.applicable * 100) : 0;
        return (
          <span className="num font-semibold">
            {r.score}/{r.applicable}
            <span className={`ml-1.5 rounded-full px-1.5 py-0.5 text-[10px] ${
              pc >= 75 ? "bg-pos-bg text-pos" : pc >= 50 ? "bg-warn-bg text-warn" : "bg-neg-bg text-neg"}`}>
              {pc}%
            </span>
          </span>
        );
      },
    },
    {
      key: "verdict", header: "Status",
      render: (r) => <StatusBadge tone={VERDICT_TONE[r.verdict]} dot>{VERDICT_LABEL[r.verdict]}</StatusBadge>,
    },
    {
      key: "breakdown", header: "Why",
      render: (r) => {
        const c = r.counts || {};
        const failed = r.failed ?? [];
        const reviewing = r.review_items ?? [];
        const missing = r.missing ?? [];
        return (
          <div className="max-w-[340px] space-y-0.5 text-[11px] leading-snug">
            <div className="flex flex-wrap gap-1">
              <CountChip n={c.pass ?? 0} label="pass" tone="bg-pos-bg text-pos" />
              <CountChip n={c.fail ?? 0} label="fail" tone="bg-neg-bg text-neg" />
              <CountChip n={c.review ?? 0} label="review" tone="bg-warn-bg text-warn" />
              <CountChip n={c.insufficient_data ?? 0} label="no data" tone="bg-surface-3 text-dim" />
              <CountChip n={c.not_applicable ?? 0} label="n/a" tone="bg-surface-3 text-faint" />
            </div>
            {failed.length > 0 && (
              <div className="text-neg">✗ {failed.join(", ")}</div>
            )}
            {reviewing.length > 0 && (
              <div className="text-warn">? {reviewing.join(", ")} needs confirmation</div>
            )}
            {missing.length > 0 && failed.length === 0 && (
              <div className="text-dim">missing: {missing.slice(0, 3).join(", ")}{missing.length > 3 ? "…" : ""}</div>
            )}
            {r.blocked_reasons.length > 0 && (
              <div className="text-neg">blocked: {r.blocked_reasons.join(", ")}</div>
            )}
          </div>
        );
      },
    },
    {
      key: "qual", header: "Qualified",
      render: (r) =>
        r.qualified ? <StatusBadge tone="pos">yes</StatusBadge> : <span className="text-faint">—</span>,
    },
  ];

  const detailGroups = useMemo(() => {
    if (!detail) return [];
    const byKey = new Map(detail.criteria.map((c) => [c.key, c]));
    const used = new Set<string>();
    const groups = GROUPS.map((g) => ({
      title: g.title,
      criteria: g.keys
        .map((k) => byKey.get(k))
        .filter((c): c is Criterion => {
          if (!c) return false;
          used.add(c.key);
          return true;
        }),
    })).filter((g) => g.criteria.length > 0);
    const rest = detail.criteria.filter((c) => !used.has(c.key));
    if (rest.length) groups.push({ title: "Other", criteria: rest });
    return groups;
  }, [detail]);

  return (
    <div className="space-y-4">
      <PageHeader info='Green Zone screen: 20 criteria scored 0–20. A passing score means a candidate is worth deeper research — not that it should be bought.'
        title="Green Zone Screener"
        subtitle="20-criteria fundamental screen — deterministic, evidence-backed"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <FilterSelect
              value={scope}
              onChange={setScope}
              options={[
                { value: "approved", label: "Approved universe" },
                { value: "eligible", label: "Eligible universe" },
                { value: "global", label: "Global (all tracked)" },
                { value: "custom", label: "Custom tickers" },
              ]}
            />
            {scope === "custom" && (
              <input
                value={customSyms}
                onChange={(e) => setCustomSyms(e.target.value.toUpperCase())}
                onKeyDown={(e) => e.key === "Enter" && runScreen()}
                placeholder="AAPL, MSFT, LULU…"
                className="w-52 rounded-md border border-border bg-surface-2 px-2.5 py-1.5 text-[12px] font-semibold uppercase text-text placeholder:normal-case placeholder:text-faint" />
            )}
            <button
              onClick={runScreen}
              disabled={running}
              className="flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-[13px] font-semibold text-[#0b0f1a] transition enabled:hover:brightness-110 disabled:opacity-50"
            >
              <Play className="size-3.5" /> {running ? "Running…" : "Run screen"}
            </button>
          </div>
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
          onRowClick={(r) => openDetail(r.symbol)}
          empty="No screening run yet — click Run screen"
        />
      </SectionCard>

      <Drawer open={!!detail} onClose={() => setDetail(null)} wide
        title={detail ? `${detail.symbol}${detail.name ? ` — ${detail.name}` : ""}` : ""}>
        {detail && (
          <div className="space-y-4">
            {/* verdict banner — what the screen concluded and why */}
            <div className="glass-tile space-y-3 p-4">
              <div className="flex flex-wrap items-center gap-3">
                <StatusBadge tone={VERDICT_TONE[detail.verdict]} dot>
                  {VERDICT_LABEL[detail.verdict]}
                </StatusBadge>
                <span className="num text-lg font-bold">
                  {detail.score}<span className="text-faint">/{detail.applicable}</span>
                  <span className="ml-1.5 text-sm text-dim">
                    ({detail.applicable
                      ? Math.round(detail.score / detail.applicable * 100)
                      : 0}%)
                  </span>
                </span>
                <span className="text-[12px] text-faint">
                  score of applicable criteria{detail.sector ? ` · ${detail.sector}` : ""}
                </span>
                {detail.band === "conditional" && (
                  <span className="rounded-full bg-warn-bg px-2 py-0.5 text-[11px] font-semibold text-warn">
                    15–17 conditional band
                  </span>
                )}
                {detail.band === "strong" && detail.verdict === "review" && (
                  <span className="rounded-full bg-warn-bg px-2 py-0.5 text-[11px] font-semibold text-warn">
                    awaiting confirmation
                  </span>
                )}
              </div>
              {/* score bar — pass/review/fail proportions at a glance */}
              {detail.criteria.length > 0 && (() => {
                const n = detail.criteria.length;
                const seg = (st: string, cls: string) => {
                  const w = detail.criteria.filter((c) => c.status === st).length / n * 100;
                  return w > 0 ? <div className={`${cls}`} style={{ width: `${w}%` }} /> : null;
                };
                return (
                  <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-surface-3">
                    {seg("pass", "bg-pos")}{seg("review", "bg-warn")}
                    {seg("fail", "bg-neg")}{seg("insufficient_data", "bg-surface-3 brightness-150")}
                  </div>
                );
              })()}
              <p className="text-[13px] leading-snug text-dim">
                {VERDICT_MEANING[detail.verdict]}
              </p>
              <div className="flex flex-wrap gap-4 border-t border-border/50 pt-2 text-[11px] text-faint">
                <span>policy v{detail.policy_version}</span>
                <span>mandate v{detail.mandate_version ?? "—"}</span>
                <span>screened {fmtTime(detail.as_of)}</span>
                {detail.data_freshness && <span>fundamentals to {fmtDate(detail.data_freshness)}</span>}
                {detail.blocked_reasons.length > 0 && (
                  <span className="text-neg">blocked: {detail.blocked_reasons.join(", ")}</span>
                )}
              </div>
            </div>

            {/* criteria grouped the way a trader reads a quality screen */}
            {detailGroups.map((g) => (
              <div key={g.title}>
                <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-faint">
                  {g.title}
                </div>
                <ul className="grid gap-2 xl:grid-cols-2">
                  {g.criteria.map((c) => (
                    <li key={c.key}
                      className={`glass-tile px-3 py-2.5 ${c.status === "fail" ? "border-neg/40" : c.status === "review" ? "border-warn/40" : ""}`}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[13px] font-medium">
                          {c.name}
                          {c.industry_variant && (
                            <span className="ml-1.5 text-[10px] text-faint">(sector variant)</span>
                          )}
                          {c.review_required && (
                            <span className="ml-1.5 text-[10px] text-warn">needs human sign-off</span>
                          )}
                        </span>
                        <span className="flex shrink-0 items-center gap-2">
                          <span className="num text-[12px] text-dim">{c.score}</span>
                          <StatusBadge tone={CRIT_TONE[c.status] ?? "neutral"}>
                            {CRIT_STATUS_LABEL[c.status] ?? c.status.replace(/_/g, " ")}
                          </StatusBadge>
                        </span>
                      </div>
                      <p className="mt-1 text-[12px] leading-snug text-text/90">
                        {explain(c)}
                      </p>
                      <div className="num mt-1 text-[10px] uppercase tracking-wide text-faint">
                        {c.formula}
                      </div>
                      {c.status === "review" && (
                        <button
                          onClick={() => confirmCriterion(c.key)}
                          disabled={confirming === c.key}
                          className="mt-1.5 rounded-full border border-warn/50 px-2.5 py-0.5 text-[11px] font-semibold text-warn transition hover:bg-warn/10 disabled:opacity-50">
                          {confirming === c.key
                            ? "Confirming…"
                            : "Confirm evidence → count as pass"}
                        </button>
                      )}
                      {evidenceChips(c).length > 0 && (
                        <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 border-t border-border/40 pt-1.5">
                          {evidenceChips(c).map(({ k, v }) => (
                            <span key={k} className="num text-[10.5px] text-dim">
                              <span className="text-faint">{k}:</span> {v}
                            </span>
                          ))}
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </Drawer>
    </div>
  );
}
