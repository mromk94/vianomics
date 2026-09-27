"use client";

import { useEffect, useState } from "react";
import { Calculator } from "lucide-react";

import { apiGet, apiPost, ApiError } from "@/lib/api";
import { fmtNum, fmtTime } from "@/lib/format";
import { DataTable, type Column } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { SearchInput } from "@/components/ui/search-input";
import { SectionCard } from "@/components/ui/section-card";
import { SkeletonRows } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";

interface Anchors {
  symbol: string;
  sector: string | null;
  archetype: string;
  eps: number | null;
  fcf: number | null;
  shares: number | null;
  net_debt: number;
  growth: Record<string, number | null>;
  roic: number | null;
  history: Record<string, Record<string, number>>;
}

interface Run {
  id: string;
  version: number;
  methodology: string;
  inputs: { price: number; growth: number; years: number; discount_rate: number; terminal_growth: number; mos: number; scenario: string; anchors: Anchors };
  outputs: {
    five_numbers?: Record<string, { value: number | null; pass: boolean }>;
    rule1?: { sticker_price?: number; buy_price?: number; future_eps?: number; error?: string; assumptions?: Record<string, number> };
    dcf?: { per_share?: number; enterprise_value?: number; pv_terminal_value?: number; error?: string };
    reverse_dcf?: { implied_growth?: number; solved?: boolean; note?: string; error?: string };
    mos?: { discount: number; underpriced: boolean };
    sensitivity?: { growths: number[]; discount_rates: number[]; grid: (number | null)[][] };
    sector_valuation?: { metrics: Record<string, number | null> };
    missing?: string[];
  };
  created_at: string | null;
}

const MONEY = (v?: number | null) =>
  v == null ? "—" : `$${fmtNum(v, 2)}`;
const PCT = (v?: number | null) =>
  v == null ? "—" : `${(v * 100).toFixed(1)}%`;

export function ValuationPage() {
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState<{ symbol: string; name: string }[]>([]);
  const [symbol, setSymbol] = useState<string | null>(null);
  const [anchors, setAnchors] = useState<Anchors | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [versions, setVersions] = useState<{ id: string; version: number; scenario: string; sticker: number | null; dcf: number | null; created_at: string }[]>([]);
  const [form, setForm] = useState({ price: "180", growth: "0.15", years: "10", discount_rate: "0.10", terminal_growth: "0.025", mos: "0.30" });
  const [scenario, setScenario] = useState<"bear" | "base" | "bull">("base");
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!query) { setOptions([]); return; }
    const t = setTimeout(() => {
      apiGet<{ symbol: string; name: string }[]>(`/api/v1/universe/instruments?q=${query}&limit=8`)
        .then(setOptions).catch(() => {});
    }, 250);
    return () => clearTimeout(t);
  }, [query]);

  const select = async (sym: string) => {
    setSymbol(sym); setQuery(sym); setOptions([]); setLoading(true);
    setNotice(null);
    try {
      const a = await apiGet<Anchors>(`/api/v1/valuation-engine/inputs/${sym}`);
      setAnchors(a);
      if (a.growth.revenue) setForm((f) => ({ ...f, growth: a.growth.revenue!.toFixed(3) }));
      apiGet<Run>(`/api/v1/valuation-engine/${sym}`).then(setRun).catch(() => setRun(null));
      apiGet<typeof versions>(`/api/v1/valuation-engine/${sym}/versions`).then(setVersions).catch(() => {});
    } catch (e) { setNotice((e as Error).message); }
    setLoading(false);
  };

  const runVal = async () => {
    if (!symbol) return;
    setNotice(null);
    try {
      const r = await apiPost<Run>(`/api/v1/valuation-engine/run/${symbol}`, {
        price: Number(form.price), growth: Number(form.growth),
        years: Number(form.years), discount_rate: Number(form.discount_rate),
        terminal_growth: Number(form.terminal_growth),
        mos: Number(form.mos), scenario,
      });
      setRun(r);
      apiGet<typeof versions>(`/api/v1/valuation-engine/${symbol}/versions`).then(setVersions).catch(() => {});
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : (e as Error).message);
    }
  };

  const o = run?.outputs;
  const fiveCols: Column<{ k: string; v: { value: number | null; pass: boolean } }>[] = [
    { key: "n", header: "Number", render: (r) => <span className="capitalize">{r.k.replace(/_/g, " ")}</span> },
    { key: "v", header: "Value", align: "right", render: (r) => <span className="num">{PCT(r.v.value)}</span> },
    { key: "p", header: "Pass", render: (r) => <StatusBadge tone={r.v.pass ? "pos" : r.v.value === null ? "info" : "neg"}>{r.v.value === null ? "n/a" : r.v.pass ? "PASS" : "FAIL"}</StatusBadge> },
  ];

  return (
    <div className="space-y-4">
      <PageHeader title="Valuation Lab" subtitle="Rule #1 Sticker · DCF · Reverse DCF — deterministic, versioned" />
      {notice && <div className="glass border-warn/40 p-3 text-[13px] text-warn">{notice}</div>}

      <SectionCard title="Security" className="rise">
        <div className="relative max-w-sm">
          <SearchInput value={query} onChange={setQuery} placeholder="Search ticker…" />
          {options.length > 0 && (
            <div className="glass absolute z-20 mt-1 w-full overflow-hidden rounded-xl border border-border">
              {options.map((o2) => (
                <button key={o2.symbol} onClick={() => select(o2.symbol)}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] hover:bg-surface-2">
                  <span className="font-semibold text-accent">{o2.symbol}</span>
                  <span className="truncate text-dim">{o2.name}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </SectionCard>

      {anchors && (
        <>
          {/* anchors + assumption editor */}
          <div className="rise rise-1 grid gap-3 lg:grid-cols-2">
            <SectionCard title={`${anchors.symbol} — Fundamental anchors`}
              action={`${anchors.archetype} · ${anchors.sector ?? "unclassified"}`}>
              <div className="grid grid-cols-2 gap-2 text-[13px] sm:grid-cols-3">
                {[
                  ["EPS (latest)", MONEY(anchors.eps)],
                  ["FCF (latest)", anchors.fcf ? `$${fmtNum(anchors.fcf / 1e9, 1)}B` : "—"],
                  ["Shares", anchors.shares ? `${fmtNum(anchors.shares / 1e9, 2)}B` : "—"],
                  ["Net debt", `$${fmtNum(anchors.net_debt / 1e9, 1)}B`],
                  ["Rev CAGR", PCT(anchors.growth.revenue)],
                  ["Equity CAGR", PCT(anchors.growth.equity)],
                  ["FCF CAGR", PCT(anchors.growth.fcf)],
                  ["ROIC", PCT(anchors.roic)],
                ].map(([l, v]) => (
                  <div key={l as string} className="glass-tile px-3 py-2">
                    <div className="text-[10px] tracking-wider text-dim uppercase">{l}</div>
                    <div className="num mt-0.5 font-semibold">{v}</div>
                  </div>
                ))}
              </div>
            </SectionCard>

            <SectionCard title="Assumptions" action={
              <div className="flex gap-1">
                {(["bear", "base", "bull"] as const).map((s) => (
                  <button key={s} onClick={() => {
                    setScenario(s);
                    if (anchors?.growth.revenue) {
                      const mult = s === "bear" ? 0.6 : s === "bull" ? 1.5 : 1;
                      setForm((f) => ({ ...f, growth: (anchors.growth.revenue! * mult).toFixed(3) }));
                    }
                  }}
                    className={`rounded-full px-3 py-1 text-[11px] font-medium capitalize transition ${scenario === s ? "bg-accent text-[#0b0f1a]" : "border border-border text-dim hover:text-text"}`}>
                    {s}
                  </button>
                ))}
              </div>}>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                {[
                  ["price", "Price $"], ["growth", "FCF/EPS growth"],
                  ["years", "Years"], ["discount_rate", "Discount rate"],
                  ["terminal_growth", "Terminal growth"], ["mos", "Margin of safety"],
                ].map(([k, l]) => (
                  <label key={k} className="text-[11px] tracking-wide text-dim">
                    {l}
                    <input value={form[k as keyof typeof form]}
                      onChange={(e) => setForm({ ...form, [k]: e.target.value })}
                      className="num mt-1 block w-full rounded-md border border-border bg-surface-2 px-2.5 py-1.5 text-text" />
                  </label>
                ))}
              </div>
              <button onClick={runVal}
                className="mt-3 flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-[13px] font-semibold text-[#0b0f1a] transition hover:brightness-110">
                <Calculator className="size-3.5" /> Run valuation
              </button>
            </SectionCard>
          </div>

          {run && o && (
            <>
              {/* outputs strip */}
              <div className="rise rise-2 grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-6">
                {[
                  ["Sticker Price", MONEY(o.rule1?.sticker_price)],
                  ["Buy Price", MONEY(o.rule1?.buy_price)],
                  ["DCF / share", MONEY(o.dcf?.per_share)],
                  ["MOS", o.mos ? PCT(o.mos.discount) : "—"],
                  ["Implied growth", o.reverse_dcf?.implied_growth != null ? PCT(o.reverse_dcf.implied_growth) : "—"],
                  ["Price", MONEY(run.inputs.price)],
                ].map(([l, v]) => (
                  <div key={l as string} className="glass p-4">
                    <div className="text-[10px] tracking-wider text-dim uppercase">{l}</div>
                    <div className="num mt-1 text-xl font-semibold">{v}</div>
                  </div>
                ))}
              </div>

              {(o.rule1?.error || o.dcf?.error || (o.missing?.length ?? 0) > 0) && (
                <div className="glass border-warn/40 p-3 text-[12px] text-warn">
                  {[o.rule1?.error, o.dcf?.error, ...(o.missing ?? [])].filter(Boolean).join(" · ")}
                </div>
              )}

              <div className="rise rise-3 grid gap-3 lg:grid-cols-2">
                {/* Five Numbers */}
                <SectionCard title="Rule #1 — Five Numbers">
                  {o.five_numbers ? (
                    <DataTable
                      columns={fiveCols}
                      rows={Object.entries(o.five_numbers)
                        .filter(([k]) => k !== "all_pass")
                        .map(([k, v]) => ({ k, v }))}
                      rowKey={(r) => r.k}
                    />
                  ) : <EmptyState title="No data" />}
                </SectionCard>

                {/* Sensitivity */}
                <SectionCard title="Sensitivity — IV per share (growth × discount)">
                  {o.sensitivity ? (
                    <div className="overflow-x-auto">
                      <table className="w-full text-[12px]">
                        <thead>
                          <tr>
                            <th className="pb-2 text-left text-faint">g \ r</th>
                            {o.sensitivity.discount_rates.map((r) => (
                              <th key={r} className="num pb-2 text-right text-faint">{PCT(r)}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {o.sensitivity.growths.map((g, gi) => (
                            <tr key={g} className="border-t border-border/50">
                              <td className="num py-1.5 text-dim">{PCT(g)}</td>
                              {o.sensitivity!.grid[gi].map((v, ri) => (
                                <td key={ri} className={`num py-1.5 text-right ${v != null && v >= run.inputs.price ? "text-pos" : "text-text/80"}`}>
                                  {v == null ? "—" : fmtNum(v, 0)}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : <EmptyState title="No sensitivity" />}
                </SectionCard>
              </div>

              {/* history + provenance + versions */}
              <div className="rise grid gap-3 lg:grid-cols-3">
                <SectionCard title="History (EDGAR XBRL)">
                  {(["revenue", "net_income", "fcf"] as const).map((k) => (
                    <div key={k} className="mb-2">
                      <div className="text-[10px] tracking-wider text-dim uppercase">{k.replace("_", " ")}</div>
                      <div className="num flex flex-wrap gap-x-3 text-[12px]">
                        {Object.entries(run.inputs.anchors.history[k] ?? {}).map(([d, v]) => (
                          <span key={d}><span className="text-faint">{d.slice(0, 4)}</span> ${fmtNum(v / 1e9, 1)}B</span>
                        ))}
                      </div>
                    </div>
                  ))}
                </SectionCard>
                <SectionCard title="Sector metrics" action={anchors.archetype}>
                  {Object.entries(o.sector_valuation?.metrics ?? {}).map(([k, v]) => (
                    <div key={k} className="flex justify-between py-0.5 text-[12px]">
                      <span className="text-dim">{k}</span>
                      <span className="num">{v == null ? "—" : fmtNum(v, 2)}</span>
                    </div>
                  ))}
                </SectionCard>
                <SectionCard title={`Versions · ${run.methodology}`}>
                  {versions.length === 0 ? <EmptyState title="No versions" /> : (
                    <ul className="space-y-1 text-[12px]">
                      {versions.map((v) => (
                        <li key={v.id} className="glass-tile flex justify-between px-3 py-1.5">
                          <span>v{v.version} · {v.scenario}</span>
                          <span className="num text-dim">sticker {MONEY(v.sticker)} · dcf {MONEY(v.dcf)}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </SectionCard>
              </div>
            </>
          )}
        </>
      )}
      {!anchors && !loading && (
        <EmptyState title="Select a security" hint="Fundamental anchors load automatically; adjust assumptions and run." />
      )}
      {loading && <SkeletonRows />}
    </div>
  );
}
