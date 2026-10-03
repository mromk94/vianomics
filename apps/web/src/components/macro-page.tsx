"use client";

import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";

import { apiGet, apiPost, ApiError } from "@/lib/api";
import { fmtNum, fmtTime } from "@/lib/format";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { SkeletonRows } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";

interface Regime {
  as_of: string;
  rules_version: string;
  econ_regime: string;
  market_regime: string;
  fear_greed: number | null;
  fg_components: Record<string, number>;
  overlay: string | null;
  vix: number | null;
  vix_band: string | null;
  features: Record<string, { value: number | null; observed_at?: string | null; stale: boolean; window?: number[]; sma200?: number | null; above?: boolean | null }>;
  rule_hits: string[];
  stale_inputs: string[];
  sector_preferences: Record<string, string>;
  notes: string[];
  persisted?: boolean;
}

interface Rotation {
  regime: string;
  favored_sectors: string[];
  all_mappings: Record<string, string[]>;
  mandate_constraints: { max_sector_pct: number; min_sectors: number; reassessment: string };
}

const ECON_ORDER = ["recovery", "expansion", "slowdown", "recession"];
const MKT_ORDER = ["risk_on", "neutral", "risk_off"];
const TONE: Record<string, "pos" | "warn" | "neg" | "info"> = {
  recovery: "pos", expansion: "pos", slowdown: "warn", recession: "neg",
  risk_on: "pos", neutral: "info", risk_off: "neg",
  accumulation: "pos", cautious_accumulation: "info",
  reduce: "warn", aggressive_risk_reduction: "neg",
  normal: "pos", tighten_risk: "warn", reduce_position_size: "warn",
  insufficient_data: "info",
};

const IND_LABEL: Record<string, string> = {
  UNRATE: "Unemployment", PAYEMS: "Payrolls", CPIAUCSL: "CPI",
  PPIACO: "PPI", INDPRO: "Ind. Production", UMCSENT: "Sentiment",
  T10Y2Y: "Yield curve", FEDFUNDS: "Fed funds", BAMLH0A0HYM2: "HY spread",
  DTWEXBGS: "Dollar", DCOILWTICO: "WTI", WALCL: "Fed BS", VIXCLS: "VIX",
};

export function MacroPage() {
  const [r, setR] = useState<Regime | null>(null);
  const [rot, setRot] = useState<Rotation | null>(null);
  const [hist, setHist] = useState<{ as_of: string; econ: string; market: string; fear_greed: number | null; overlay: string | null }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setError(null);
    Promise.all([
      apiGet<Regime>("/api/v1/macro/current"),
      apiGet<Rotation>("/api/v1/macro/sector-rotation"),
      apiGet<typeof hist>("/api/v1/macro/history"),
    ]).then(([rg, ro, h]) => { setR(rg); setRot(ro); setHist(h); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const runRegime = async () => {
    try {
      await apiPost("/api/v1/macro/run", {}, 90_000);
      setNotice(null);
      load();
    } catch (e) {
      setNotice(e instanceof ApiError && e.status === 401
        ? "Sign in (Settings) to persist regime runs."
        : (e as Error).message);
    }
  };

  const econIdx = r ? ECON_ORDER.indexOf(r.econ_regime) : -1;

  return (
    <div className="space-y-4">
      <PageHeader info='Economic regime (expansion/slowdown/recession) and market risk appetite from FRED, VIX and Fear & Greed. Regime feeds sizing and sector rotation.'
        title="Macro Regime"
        subtitle={`rules ${r?.rules_version ?? "—"} · overlays are portfolio inputs, not signals`}
        actions={
          <button onClick={runRegime}
            className="flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-[13px] font-semibold text-[#0b0f1a] transition hover:brightness-110">
            <RefreshCw className="size-3.5" /> Run regime
          </button>
        }
        meta={r ? `as of ${fmtTime(r.as_of)}${r.persisted ? " · persisted" : " · live compute"}` : undefined}
      />
      {notice && <div className="glass border-warn/40 p-3 text-[13px] text-warn">{notice}</div>}
      {error && <ErrorState title="Cannot reach the VAIIP API" detail={error} onRetry={load} />}
      {loading && <SkeletonRows />}

      {r && (
        <>
          {/* regime track */}
          <div className="rise grid gap-3 lg:grid-cols-2">
            <SectionCard title="Economic Regime">
              <div className="flex items-center gap-0">
                {ECON_ORDER.map((e, i) => (
                  <div key={e} className="flex flex-1 items-center">
                    <div className={`flex h-9 flex-1 items-center justify-center rounded-full text-[12px] font-medium capitalize transition
                      ${r.econ_regime === e ? "bg-accent text-[#0b0f1a]" : "border border-border text-dim"}`}>
                      {e}
                    </div>
                    {i < ECON_ORDER.length - 1 && <div className="h-px w-2 bg-border" />}
                  </div>
                ))}
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5 text-[11px]">
                {r.rule_hits.map((h) => (
                  <span key={h} className="rounded-full bg-surface-2 px-2 py-0.5 text-dim">{h}</span>
                ))}
              </div>
              {r.stale_inputs.length > 0 && (
                <div className="mt-2 text-[11px] text-warn">
                  stale/missing inputs: {r.stale_inputs.join(", ")}
                </div>
              )}
            </SectionCard>

            <SectionCard title="Market Regime & Overlays">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <div className="glass-tile p-3">
                  <div className="text-[10px] tracking-wider text-dim uppercase">Market</div>
                  <StatusBadge tone={TONE[r.market_regime]} dot>{r.market_regime.replace("_", " ")}</StatusBadge>
                </div>
                <div className="glass-tile p-3">
                  <div className="text-[10px] tracking-wider text-dim uppercase">F&G proxy</div>
                  <div className="num text-lg font-semibold">{r.fear_greed ?? "—"}</div>
                  {r.overlay && <StatusBadge tone={TONE[r.overlay]}>{r.overlay.replace(/_/g, " ")}</StatusBadge>}
                </div>
                <div className="glass-tile p-3">
                  <div className="text-[10px] tracking-wider text-dim uppercase">VIX</div>
                  <div className="num text-lg font-semibold">{r.vix ?? "—"}</div>
                  {r.vix_band && <StatusBadge tone={TONE[r.vix_band]}>{r.vix_band.replace(/_/g, " ")}</StatusBadge>}
                </div>
                <div className="glass-tile p-3">
                  <div className="text-[10px] tracking-wider text-dim uppercase">Breadth (SMA50)</div>
                  <div className="num text-lg font-semibold">
                    {r.features.breadth_50?.value != null
                      ? `${Math.round(r.features.breadth_50.value * 100)}%` : "—"}
                  </div>
                </div>
              </div>
              {Object.keys(r.fg_components).length > 0 && (
                <div className="mt-3 flex flex-wrap gap-x-4 text-[11px] text-faint">
                  F&G components: {Object.entries(r.fg_components).map(([k, v]) => `${k} ${Math.round(v)}`).join(" · ")}
                </div>
              )}
            </SectionCard>
          </div>

          {/* indicators */}
          <SectionCard title="Indicators" className="rise rise-1"
            action="value · observed_at · freshness">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-4">
              {Object.entries(IND_LABEL).map(([code, label]) => {
                const f = r.features[code];
                return (
                  <div key={code} className={`glass-tile px-3 py-2 ${f?.stale ? "opacity-60" : ""}`}>
                    <div className="flex items-center justify-between">
                      <span className="text-[11px] text-dim">{label}</span>
                      {f?.stale && <StatusBadge tone="warn">stale</StatusBadge>}
                    </div>
                    <div className="num mt-0.5 text-base font-semibold">
                      {f?.value != null ? fmtNum(f.value, 2) : "—"}
                    </div>
                    <div className="num text-[10px] text-faint">
                      {f?.observed_at ? fmtTime(f.observed_at) : "no data"}
                    </div>
                  </div>
                );
              })}
            </div>
          </SectionCard>

          {/* sector rotation + history */}
          <div className="rise rise-2 grid gap-3 lg:grid-cols-2">
            <SectionCard title={`Sector Rotation — ${rot?.regime ?? ""}`}
              action={`max ${rot?.mandate_constraints.max_sector_pct}%/sector · min ${rot?.mandate_constraints.min_sectors} sectors · ${rot?.mandate_constraints.reassessment ?? ""} review`}>
              <div className="mb-2 flex flex-wrap gap-1.5">
                {(rot?.favored_sectors ?? []).map((s) => (
                  <StatusBadge key={s} tone="pos">{s}</StatusBadge>
                ))}
              </div>
              <table className="w-full text-[12px]">
                <thead>
                  <tr className="text-left text-faint">
                    <th className="pb-1.5">Regime</th><th className="pb-1.5">Favored</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(rot?.all_mappings ?? {}).map(([reg, secs]) => (
                    <tr key={reg} className={`border-t border-border/50 ${reg === rot?.regime ? "text-accent" : "text-dim"}`}>
                      <td className="py-1.5 capitalize">{reg}</td>
                      <td className="py-1.5">{secs.join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="mt-2 text-[11px] text-faint">
                Inputs to portfolio analysis — not forecasts.
              </div>
            </SectionCard>

            <SectionCard title="Run History">
              {hist.length === 0 ? (
                <EmptyState title="No persisted runs" hint="Run regime to store a snapshot." />
              ) : (
                <ul className="max-h-64 space-y-1 overflow-y-auto text-[12px]">
                  {hist.map((h, i) => (
                    <li key={i} className="glass-tile flex items-center justify-between px-3 py-1.5">
                      <span className="num text-faint">{fmtTime(h.as_of)}</span>
                      <StatusBadge tone={TONE[h.econ]}>{h.econ}</StatusBadge>
                      <StatusBadge tone={TONE[h.market]}>{h.market.replace("_", " ")}</StatusBadge>
                      <span className="num">{h.fear_greed ?? "—"}</span>
                    </li>
                  ))}
                </ul>
              )}
            </SectionCard>
          </div>
        </>
      )}
      {!r && !loading && !error && <EmptyState title="No regime data" hint="Ingest macro series, then run the classifier." />}
    </div>
  );
}
