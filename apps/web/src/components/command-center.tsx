"use client";

import { useEffect, useRef, useState } from "react";
import {
  CheckCircle2, ChevronDown, ChevronRight, Info, MinusCircle, StopCircle, XCircle,
} from "lucide-react";
import { AreaSeries, createChart } from "lightweight-charts";

import { apiGet, type CommandCenter } from "@/lib/api";
import { fmtCurrency, fmtNum, fmtPct, fmtTime } from "@/lib/format";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { MetricCard } from "@/components/ui/metric-card";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { SkeletonRows } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";
import { Sym } from "@/components/symbol-drawer";

type Data = CommandCenter;

const toneOf = (r?: string | null): "pos" | "neg" | "warn" => {
  const s = (r || "").toUpperCase();
  if (["BUY", "STRONG BUY", "APPROVE", "PASS"].includes(s)) return "pos";
  if (["SELL", "STRONG SELL", "REJECT", "BLOCK", "VETO"].includes(s)) return "neg";
  return "warn";
};
const toneIcon = (r?: string | null) =>
  toneOf(r) === "pos" ? <CheckCircle2 className="size-3.5 text-pos" /> :
  toneOf(r) === "neg" ? <XCircle className="size-3.5 text-neg" /> :
  <MinusCircle className="size-3.5 text-warn" />;
const toneBorder = { pos: "border-l-pos", neg: "border-l-neg", warn: "border-l-accent" } as const;
const toneText = { pos: "text-pos", neg: "text-neg", warn: "text-accent" } as const;
const tonePill = {
  pos: "bg-pos text-[#0b0f1a]", neg: "bg-neg text-white", warn: "bg-accent text-[#0b0f1a]",
} as const;

function Bar({ pct, className = "confidence-fill" }: { pct: number; className?: string }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-3">
      <div className={`${className} bar-anim h-full rounded-full`} style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
    </div>
  );
}

function Gauge({ value, label }: { value: number; label: string }) {
  const pct = Math.min(100, Math.max(0, value));
  const color = pct <= 25 ? "var(--neg)" : pct <= 45 ? "var(--warn)" : pct <= 65 ? "var(--accent)" : "var(--pos)";
  return (
    <div
      className="relative flex size-14 shrink-0 items-center justify-center rounded-full"
      style={{ background: `conic-gradient(${color} 0deg ${(pct / 100) * 360}deg, var(--surface-3) 0deg)` }}
      role="meter" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={label}
    >
      <div className="absolute size-10 rounded-full bg-surface-solid" />
      <span className="num relative text-base font-bold">{fmtNum(value, 0)}</span>
    </div>
  );
}

function RegimeSteps({ active }: { active: string | null }) {
  const steps = ["Recovery", "Expansion", "Slowdown", "Recession"];
  return (
    <div className="mt-3 flex rounded-full bg-surface-2 p-1">
      {steps.map((s) => (
        <span
          key={s}
          className={`flex-1 rounded-full py-1 text-center text-[11px] transition-all duration-300 ${
            active?.toLowerCase() === s.toLowerCase() ? "bg-accent font-semibold text-[#0b0f1a]" : "text-dim"
          }`}
        >
          {s}
        </span>
      ))}
    </div>
  );
}

function ScaleBar({ active, labels }: { active: string; labels: string[] }) {
  const cls = (l: string) =>
    l.startsWith("Accum") ? "bg-pos text-[#0b0f1a] font-bold"
    : l === "Reduce" ? "bg-warn text-[#0b0f1a] font-bold"
    : l === "Risk-off" ? "bg-neg text-white font-bold"
    : "bg-accent text-[#0b0f1a] font-bold";
  return (
    <div className="flex flex-1 items-center gap-0.5">
      {labels.map((l, i) => (
        <span key={i} className="flex flex-1 items-center">
          <span className={`w-full rounded py-0.5 text-center text-[10px] transition-all ${l === active ? cls(l) : "text-faint"}`}>
            {l}
          </span>
          {i < labels.length - 1 && <span className="mx-0.5 text-border-strong">|</span>}
        </span>
      ))}
    </div>
  );
}

export function CommandCenter() {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [source, setSource] = useState<string | null>(null);
  const [extSources, setExtSources] = useState<{ source: string; label: string }[]>([]);

  const load = () => {
    if (source === null) return;
    setLoading(true);
    setError(null);
    apiGet<Data>(`/api/v1/command-center?source=${source}`, 60000)
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };
  // resolve which sources exist first — only then choose a default
  // (connected accounts → "all"), so the portfolio never paints empty
  useEffect(() => {
    apiGet<{ source: string; label: string }[]>("/api/v1/external/sources")
      .then((xs) => {
        setExtSources(xs);
        setSource((s) => s ?? (xs.length ? "all" : "internal"));
      })
      .catch(() => setSource((s) => s ?? "internal"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [source]);

  if (error) {
    return (
      <div>
        <PageHeader title="Command Center" subtitle="Daily briefing — the decision pipeline at a glance" />
        <ErrorState
          title="Cannot reach the VAIIP API"
          detail={`${error}. Start the backend (uvicorn on :8000) or check NEXT_PUBLIC_API_URL.`}
          onRetry={load}
        />
      </div>
    );
  }

  const demo = new Set(data?.demo_sections ?? []);
  const isDemo = (k: string) => demo.has(k);
  const macro = data ? Object.entries(data.regime.macro_indicators) : [];
  const fg = data?.regime.fear_greed;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Command Center"
        subtitle="Daily briefing — the decision pipeline at a glance"
        demo={demo.size > 0}
        meta={data ? `Data as of ${fmtTime(data.generated_at)}` : undefined}
        actions={
          <select value={source ?? "internal"} onChange={(e) => setSource(e.target.value)}
            className="rounded-lg border border-border bg-surface-solid px-2.5 py-1.5 text-[12px] text-text">
            <option value="internal">Internal portfolio</option>
            {extSources.map((x) => (
              <option key={x.source} value={x.source}>{x.label}</option>))}
            {extSources.length > 0 && <option value="all">All sources combined</option>}
          </select>
        }
      />
      {loading && <SkeletonRows />}

      {data && (
        <>
          {/* portfolio strip — collapsible */}
          <PortfolioStrip p={data.portfolio} demo={isDemo("portfolio")} />
          {(data.portfolio.holdings?.length ?? 0) > 0 && (
            <div className="rise rise-1 glass px-4 py-2.5 flex flex-wrap gap-x-6 gap-y-1 text-[12px]">
              <span className="text-dim">Holdings</span>
              {data.portfolio.holdings!.slice(0, 8).map((h) => (
                <span key={h.symbol} className="flex items-center gap-1.5">
                  <Sym s={h.symbol} />
                  <span className="num">${fmtNum(h.market_value)}</span>
                  <span className={`num text-[11px] ${(h.unrealized ?? 0) >= 0 ? "text-pos" : "text-neg"}`}>
                    {h.unrealized != null ? `${h.unrealized >= 0 ? "+" : ""}${fmtNum(h.unrealized)}` : ""}
                  </span>
                  <span className="text-faint">{h.source}</span>
                </span>
              ))}
              {(data.portfolio.holdings!.length ?? 0) > 8 &&
                <span className="text-faint">+{data.portfolio.holdings!.length - 8} more</span>}
            </div>
          )}

          {/* CIO Recommendation */}
          <SectionCard title="CIO Recommendation" demo={isDemo("cio")} className="rise rise-2"
            action={data.cio.confidence != null ? `Confidence ${data.cio.confidence}%` : undefined}>
            {!data.cio.rating ? (
              <EmptyState title="No CIO recommendation yet" hint="Agent runs and synthesis have not produced a verdict." />
            ) : (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-4">
                  <span className={`rounded-full px-5 py-1 text-lg font-bold tracking-wide ${tonePill[toneOf(data.cio.rating)]}`}>
                    {data.cio.rating}
                  </span>
                  <span className="text-sm text-dim">
                    Expected Return <strong className="text-text">{fmtPct(data.cio.expected_return_pct, 0)}</strong>
                    {"  |  "}MOS <strong className="text-text">{fmtPct(data.cio.mos_pct, 0)}</strong>
                  </span>
                </div>
                {data.cio.confidence != null && <Bar pct={data.cio.confidence} />}
                <div className="flex flex-wrap gap-x-3.5 gap-y-1.5">
                  {data.agents.map((a) => (
                    <span key={a.agent}
                      className={`glass-tile flex items-center gap-1.5 rounded-full border-l-[3px] px-2.5 py-0.5 text-[12px] ${toneBorder[toneOf(a.recommendation)]}`}>
                      {toneIcon(a.recommendation)} {a.agent}
                    </span>
                  ))}
                </div>
                <div className="border-t border-border pt-2.5 text-[13px] text-dim">
                  <strong className="text-neg">Risk Manager:</strong> VETO: {data.cio.risk_veto ?? "None"}
                  {data.cio.kill_conditions.length > 0 && (
                    <>{"  |  "}<strong>Kill Conditions:</strong> {data.cio.kill_conditions.join(", ")}</>
                  )}
                </div>
              </div>
            )}
          </SectionCard>

          <div className="grid gap-4 xl:grid-cols-2">
            {/* Economic & Market Regime */}
            <SectionCard title="Economic & Market Regime" demo={isDemo("regime")} className="rise rise-3" action="Macro overlay">
              {!data.regime.economic_regime ? (
                <EmptyState title="No macro data" hint="Awaiting macro-regime engine + data ingestion." />
              ) : (
                <>
                  <div className="flex items-center gap-3">
                    <span className="rounded-full bg-pos px-5 py-1 text-[15px] font-bold tracking-wide text-[#0b0f1a]">
                      {data.regime.economic_regime.toUpperCase()}
                    </span>
                    <span className="text-sm text-dim">
                      Market Regime:{" "}
                      <strong className={toneOf(data.regime.market_regime) === "pos" ? "text-pos" : "text-text"}>
                        {data.regime.market_regime}
                      </strong>
                    </span>
                  </div>
                  <RegimeSteps active={data.regime.economic_regime} />
                  <div className="mt-3 grid grid-cols-3 gap-2">
                    {macro.map(([k, v]) => (
                      <div key={k} className="glass-tile px-3 py-2">
                        <div className="text-[10px] tracking-wider text-dim uppercase">{k}</div>
                        <div className={`num text-base font-semibold ${
                          String(v).startsWith("-") ? "text-neg" : "text-pos"}`}>
                          {v}
                        </div>
                      </div>
                    ))}
                  </div>
                  <div className="mt-3 flex items-center gap-1.5 border-t border-border pt-2.5 text-[12px] text-dim">
                    <Info className="size-3 text-accent" />
                    Sector preference: Technology, Consumer Discretionary, Industrials
                  </div>
                </>
              )}
            </SectionCard>

            {/* Sector Performance */}
            <SectionCard title="Sector Performance (Market)" demo={isDemo("sectors")} className="rise rise-3" action="Relative strength">
              {data.sectors.length === 0 ? (
                <EmptyState title="No sector data" hint="Awaiting market-data provider." />
              ) : (
                <table className="w-full text-[13px]">
                  <thead>
                    <tr className="border-b border-border text-left text-[10px] tracking-wider text-dim uppercase">
                      <th className="py-1 pr-2 font-medium">Sector</th>
                      <th className="py-1 pr-2 font-medium">Weight</th>
                      <th className="py-1 pr-2 font-medium">1D</th>
                      <th className="py-1 pr-2 font-medium">1W</th>
                      <th className="py-1 font-medium">Momentum</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.sectors.map((s) => (
                      <tr key={s.sector} className="border-b border-border/50 last:border-0">
                        <td className="py-1.5 pr-2">{s.sector}</td>
                        <td className="num py-1.5 pr-2 text-dim">{fmtPct(s.weight_pct)}</td>
                        <td className={`num py-1.5 pr-2 ${s.day_pct != null && s.day_pct >= 0 ? "text-pos" : "text-neg"}`}>
                          {fmtPct(s.day_pct)}
                        </td>
                        <td className={`num py-1.5 pr-2 ${s.week_pct != null && s.week_pct >= 0 ? "text-pos" : "text-neg"}`}>
                          {fmtPct(s.week_pct)}
                        </td>
                        <td className="py-1.5">
                          <div className="h-1 w-16 overflow-hidden rounded bg-surface-3">
                            <div
                              className={`bar-anim h-full rounded ${s.week_pct != null && s.week_pct >= 0 ? "bg-pos" : "bg-neg"}`}
                              style={{ width: `${s.momentum ?? 0}%` }}
                            />
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </SectionCard>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            {/* Market Intelligence + Timing Overlay */}
            <SectionCard title="Market Intelligence" demo={isDemo("regime") || isDemo("calendar")} className="rise rise-4" action="Sentiment & Timing">
              <div className="glass-tile mb-3 space-y-2.5 p-3.5">
                <div className="flex flex-wrap items-center gap-4">
                  {fg != null && <Gauge value={fg} label="Fear and Greed index" />}
                  <div>
                    <div className="text-[11px] tracking-wider text-dim uppercase">Fear &amp; Greed</div>
                    <div className="text-[15px] font-semibold text-accent">
                      {fg == null ? "—" : fg <= 25 ? "Extreme Fear" : fg <= 45 ? "Cautious Accumulation" : fg <= 65 ? "Neutral" : "Greed"}
                    </div>
                  </div>
                  <ScaleBar active={fg != null && fg <= 45 ? "Caution" : "Neutral"} labels={["Accum.", "Caution", "Neutral", "Reduce", "Risk-off"]} />
                </div>
                <div className="flex flex-wrap items-center gap-4">
                  <div className="text-sm font-semibold">
                    VIX: <strong className="num">{fmtNum(data.regime.vix)}</strong>
                    <span className="ml-1.5 text-[13px] font-normal text-accent">→ Tighten risk</span>
                  </div>
                  <ScaleBar active="Tighten" labels={["Normal", "Tighten", "Reduce", "Risk-off"]} />
                </div>
                <div className="flex items-center gap-1.5 border-t border-border pt-2 text-[11px] text-faint">
                  <Info className="size-3 text-accent" />
                  Overlays, not standalone trading signals.
                </div>
              </div>
              {data.calendar.length === 0 ? (
                <EmptyState title="No upcoming releases" hint="Awaiting macro calendar ingestion." />
              ) : (
                <ul className="text-[13px]">
                  {data.calendar.map((e) => (
                    <li key={e.title} className="flex justify-between border-b border-border/50 py-1.5 last:border-0">
                      <span>{e.title}</span>
                      <span className="num text-[12px] text-dim">
                        {new Date(e.at).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                        {e.detail ? ` · ${e.detail}` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </SectionCard>

            {/* Risk & Monitoring */}
            <SectionCard title="Risk & Monitoring" demo={isDemo("risk")} className="rise rise-4"
              action={`Limits: Drawdown ${fmtNum(data.risk.drawdown_limit_pct, 0)}%`}>
              <div className="grid grid-cols-2 gap-2.5">
                {[
                  { label: "Portfolio VaR (95%)", value: fmtCurrency(data.risk.var_95), tone: "text-warn" },
                  { label: "Current Drawdown", value: fmtPct(data.risk.drawdown_pct), tone: data.risk.drawdown_pct != null && data.risk.drawdown_pct > data.risk.drawdown_limit_pct ? "text-neg" : "text-pos" },
                  { label: "Single Stock Limit", value: data.risk.max_position_pct != null ? `${fmtNum(data.risk.max_position_pct)}%` : "—", tone: data.risk.max_position_pct != null && data.risk.max_position_pct > data.risk.position_limit_pct ? "text-neg" : "" },
                  { label: "Sector Limit", value: data.risk.max_sector_pct != null ? `${fmtNum(data.risk.max_sector_pct)}%` : "—", tone: data.risk.max_sector_pct != null && data.risk.max_sector_pct > data.risk.sector_limit_pct ? "text-neg" : "" },
                  { label: "Correlation (avg)", value: fmtNum(data.risk.avg_correlation, 2), tone: "" },
                  { label: "Stress Test (-10%)", value: fmtCurrency(data.risk.stress_10pct), tone: "text-warn" },
                ].map((m) => (
                  <div key={m.label} className="glass-tile px-3.5 py-2.5">
                    <div className="text-[10px] tracking-wider text-dim uppercase">{m.label}</div>
                    <div className={`num mt-0.5 text-lg font-semibold ${m.tone}`}>{m.value}</div>
                  </div>
                ))}
              </div>
              {data.risk.warnings.length > 0 && (
                <ul className="mt-3 space-y-1 border-t border-border pt-2.5">
                  {data.risk.warnings.map((w) => (
                    <li key={w} className="flex items-start gap-1.5 text-[12px] text-warn">
                      <StopCircle className="mt-0.5 size-3 shrink-0" /> {w}
                    </li>
                  ))}
                </ul>
              )}
            </SectionCard>
          </div>

          {/* Agent Outputs */}
          <SectionCard title="Agent Outputs" demo={isDemo("agents")} className="rise rise-5" action="Multi-Agent Consensus">
            {data.agents.length === 0 ? (
              <EmptyState title="No agent runs" hint="Agents produce standardized outputs once research runs." />
            ) : (
              <>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5">
                  {data.agents.map((a) => (
                    <div key={a.agent} className={`glass-tile border-l-[3px] px-3.5 py-2.5 ${toneBorder[toneOf(a.recommendation)]}`}>
                      <div className="text-[13px] font-semibold">{a.agent}</div>
                      <div className="text-[12px] text-dim">
                        {a.recommendation} · <span className="num font-semibold text-text">{a.score ?? "—"}</span>
                      </div>
                    </div>
                  ))}
                </div>
                {data.cio.conflict_note && (
                  <div className="mt-3 flex items-center gap-1.5 border-t border-border pt-2.5 text-[12px] text-dim">
                    <Info className="size-3 text-accent" /> {data.cio.conflict_note}
                  </div>
                )}
              </>
            )}
          </SectionCard>

          {/* Trade Signals & Exit Monitor */}
          <SectionCard title="Trade Signals & Exit Monitor" demo={isDemo("signals")} className="rise rise-6"
            action={data.signals.length ? `Active alerts: ${data.signals.length}` : undefined}>
            {data.signals.length === 0 ? (
              <EmptyState title="No signals" hint="Signals appear when engines detect conditions." />
            ) : (
              <div className="flex flex-wrap gap-x-6 gap-y-2">
                {data.signals.map((s, i) => (
                  <span key={i} className="glass-tile flex items-center gap-2 rounded-full px-3.5 py-1.5 text-[13px]">
                    {toneIcon(s.danger ? "SELL" : "BUY")} {s.message}
                  </span>
                ))}
              </div>
            )}
          </SectionCard>

          {/* Watchlist / Approvals / Decisions */}
          <div className="rise rise-6 grid gap-4 xl:grid-cols-3">
            <SectionCard title="Watchlist" demo={isDemo("watchlist")}>
              {data.watchlist.length === 0 ? (
                <EmptyState title="Watchlist empty" hint="Tickers land here below the Green Zone threshold." />
              ) : (
                <ul className="space-y-1.5 text-[13px]">
                  {data.watchlist.map((w) => (
                    <li key={w.ticker} className="glass-tile flex items-center justify-between px-3 py-1.5">
                      <span>
                        <Sym s={w.ticker} />
                        {w.name && <span className="ml-2 text-dim">{w.name}</span>}
                      </span>
                      <span className="num text-dim">
                        {w.green_zone_score != null && <span className="mr-2">GZ {w.green_zone_score}/20</span>}
                        {w.last_price != null && fmtCurrency(w.last_price)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </SectionCard>

            <SectionCard title="Pending Approvals" demo={isDemo("approvals")}>
              {data.approvals.length === 0 ? (
                <EmptyState title="Nothing pending" hint="Approved pipeline items await your decision." />
              ) : (
                <ul className="space-y-1.5">
                  {data.approvals.map((a) => (
                    <li key={a.id} className="glass-tile flex items-center justify-between px-3 py-2 text-[13px]">
                      <div>
                        <Sym s={a.ticker} />
                        <span className="ml-2 text-dim">{a.action}</span>
                      </div>
                      <StatusBadge tone={toneOf(a.cio_rating)}>{a.cio_rating ?? "—"}</StatusBadge>
                    </li>
                  ))}
                </ul>
              )}
            </SectionCard>

            <SectionCard title="Recent Decisions" demo={isDemo("decisions")}>
              {data.decisions.length === 0 ? (
                <EmptyState title="No decisions recorded" hint="The immutable decision log starts here." />
              ) : (
                <ul className="space-y-1.5">
                  {data.decisions.map((d) => (
                    <li key={d.id} className="glass-tile flex items-center justify-between px-3 py-2 text-[13px]">
                      <span>
                        <Sym s={d.ticker} />
                        <span className="ml-2 text-faint">{fmtTime(d.at)}</span>
                      </span>
                      <StatusBadge tone={toneOf(d.verdict)}>{d.verdict}</StatusBadge>
                    </li>
                  ))}
                </ul>
              )}
            </SectionCard>
          </div>

          {/* Provider health footer */}
          <div className="rise rise-6 flex flex-wrap items-center justify-center gap-x-5 gap-y-1.5 border-t border-border pt-3 pb-1 text-[11px] text-faint">
            {data.providers.map((p) => (
              <span key={p.name} className="flex items-center gap-1.5">
                <span
                  className={`size-1.5 rounded-full ${
                    p.status === "up" ? "bg-pos pulse-dot" : p.status === "down" ? "bg-neg" : p.status === "degraded" ? "bg-warn" : "bg-faint"
                  }`}
                />
                {p.name}
              </span>
            ))}
            <span className="w-full text-center sm:w-auto">
              VAIIP · Vianomics AI{demo.size > 0 ? " · simulated sections labeled DEMO" : ""}
            </span>
          </div>
        </>
      )}
    </div>
  );
}

/* ── collapsible portfolio strip + equity curve ── */

interface PfT { total_value: number | null; cash: number | null;
  holdings?: { symbol: string; market_value: number;
    unrealized: number | null; weight: number; sector: string | null;
    source: string }[];
  daily_pnl: number | null; daily_pnl_pct: number | null;
  unrealized_pnl: number | null }

function PortfolioStrip({ p, demo }: { p: PfT; demo: boolean }) {
  const [open, setOpen] = useState(true);
  const [curve, setCurve] = useState<{ t: string; equity: number }[]>([]);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    apiGet<{ points: { t: string; equity: number }[] }>(
      "/api/v1/portfolio/equity-curve"
    ).then((d) => setCurve(d.points)).catch(() => {});
  }, []);

  useEffect(() => {
    if (!ref.current || !curve.length) return;
    const chart = createChart(ref.current, {
      autoSize: true, height: 180,
      layout: { background: { color: "transparent" },
        textColor: "#5d6a8a", fontSize: 10 },
      grid: { vertLines: { visible: false },
        horzLines: { color: "#1a2333" } },
      timeScale: { borderVisible: false },
      rightPriceScale: { borderVisible: false },
      crosshair: { mode: 1 },
      localization: { priceFormatter: (v: number) =>
        "$" + v.toLocaleString(undefined, { maximumFractionDigits: 0 }) },
    });
    const s = chart.addSeries(AreaSeries, {
      lineColor: "#34d399", topColor: "rgba(52,211,153,0.3)",
      bottomColor: "rgba(52,211,153,0.02)", lineWidth: 2,
      priceLineVisible: false });
    s.setData(curve.map((c) => ({ time: c.t, value: c.equity })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [curve]);

  return (
    <div className="rise rise-1">
      <button onClick={() => setOpen((o) => !o)}
        className="mb-2 flex w-full items-center justify-between text-left">
        <span className="flex items-center gap-1.5 text-[11px] font-semibold tracking-wider text-dim uppercase">
          {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          Portfolio
          {demo && <span className="rounded-full border border-warn/40 bg-warn-bg px-1.5 py-px text-[9px] font-bold text-warn">DEMO</span>}
        </span>
        {!open && (
          <span className="num text-[12px] text-faint">
            NAV {fmtCurrency(p.total_value)} · P&L {fmtCurrency(p.daily_pnl)}
          </span>
        )}
      </button>
      {open && (
        <>
          <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
            <MetricCard label="Portfolio Value" value={fmtCurrency(p.total_value)} />
            <MetricCard label="Cash" value={fmtCurrency(p.cash)} />
            <MetricCard label="Daily P&L" value={fmtCurrency(p.daily_pnl)}
              sub={fmtPct(p.daily_pnl_pct)}
              tone={p.daily_pnl == null ? "neutral" : p.daily_pnl >= 0 ? "pos" : "neg"} />
            <MetricCard label="Unrealized P&L" value={fmtCurrency(p.unrealized_pnl)}
              tone={p.unrealized_pnl == null ? "neutral" : p.unrealized_pnl >= 0 ? "pos" : "neg"} />
          </div>
          {curve.length > 1 && (
            <div className="glass mt-3 p-3">
              <div className="mb-1 text-[10px] uppercase tracking-wide text-faint">
                Equity curve — reconstructed from fills × daily closes
              </div>
              <div ref={ref} style={{ height: 180 }} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
