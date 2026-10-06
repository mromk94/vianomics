"use client";

import { useEffect, useState } from "react";

import { apiGet, apiPost } from "@/lib/api";
import { fmtNum, fmtTime } from "@/lib/format";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { StatusBadge } from "@/components/ui/status-badge";
import { PyramidModal } from "@/components/pyramid-modal";

interface NewsItem { source: string; title: string; url: string | null;
  at: string | null; summary: string | null }

interface BarsResp { bars: any[]; as_of?: string | null;
  latest_stored?: string | null; stale?: boolean;
  sessions_behind?: number | null; refreshed?: boolean }

const TFS = [
  { v: "1d", label: "Daily" }, { v: "2d", label: "2-Day" },
  { v: "3d", label: "3-Day" }, { v: "1w", label: "Weekly" },
  { v: "1mo", label: "Monthly" }, { v: "1y", label: "Yearly" },
];

const tone = (v?: string | null) =>
  v === "entry_signal" || v === "pass" || v === "approve_pending_human"
    ? "pos" : v === "fail" || v === "no_trade" || v === "reject"
      ? "neg" : "info" as const;

function Pct({ v }: { v: number | null | undefined }) {
  if (v == null) return <span className="text-faint">—</span>;
  return <span className={v >= 0 ? "text-pos" : "text-neg"}>
    {v >= 0 ? "+" : ""}{(v * 100).toFixed(2)}%</span>;
}

function SigCell({ l, v }: { l: string; v: React.ReactNode }) {
  return (
    <div className="glass-tile px-3 py-2">
      <div className="text-[9px] uppercase tracking-wide text-faint">{l}</div>
      <div className="num mt-0.5 text-[13px] font-semibold">{v}</div>
    </div>
  );
}

/* doc Step-8 surface grouped by lens: trend / momentum / volatility /
   structure / volume — each group = one tile of label:value rows */
function MiGroup({ title, rows }:
  { title: string; rows: [string, React.ReactNode][] }) {
  return (
    <div className="glass-tile px-3 py-2">
      <div className="text-[9px] uppercase tracking-wide text-faint">{title}</div>
      <div className="mt-1 space-y-0.5">
        {rows.map(([l, v]) => (
          <div key={l}
            className="flex items-baseline justify-between gap-2">
            <span className="text-[10px] text-dim">{l}</span>
            <span className="num text-[11px] font-medium">{v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function SymbolPage({ symbol }: { symbol: string }) {
  const sym = symbol.toUpperCase();
  const [d, setD] = useState<any | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [news, setNews] = useState<{ items: NewsItem[]; sources: string[] } | null>(null);
  const [tf, setTf] = useState("1d");
  const [bars, setBars] = useState<any[]>([]);
  const [barsMeta, setBarsMeta] = useState<BarsResp | null>(null);
  const [barsErr, setBarsErr] = useState<string | null>(null);
  const [pyrOpen, setPyrOpen] = useState(false);
  const [finTab, setFinTab] = useState<"income" | "balance" | "cashflow" | "other">("income");
  const [finFreq, setFinFreq] = useState<"FY" | "Q">("FY");
  const [riskPrev, setRiskPrev] = useState<any | null>(null);
  const [riskCalc, setRiskCalc] = useState(false);

  useEffect(() => {
    setD(null); setErr(null);
    apiGet<any>(`/api/v1/symbol/${sym}`).then(setD)
      .catch((e) => setErr(e.message));
    setNews(null);
    apiGet<{ items: NewsItem[]; sources: string[] }>(`/api/v1/news/${sym}`).then(setNews)
      .catch(() => setNews({ items: [], sources: [] }));
  }, [sym]);

  useEffect(() => {
    setBarsErr(null);
    apiGet<BarsResp>(
      `/api/v1/technical/bars/${sym}?timeframe=${tf}&limit=1500`)
      .then((r) => { setBars(r.bars); setBarsMeta(r); })
      .catch((e) => { setBars([]); setBarsMeta(null);
                      setBarsErr(e.message); });
  }, [sym, tf]);

  // doc Steps 9–12 — eligibility + daily Trade Risk Sheet come from
  // the same preview the calculator runs (default entry = last close)
  useEffect(() => {
    setRiskPrev(null); setRiskCalc(true);
    apiPost<any>("/api/v1/risk/pyramid/preview",
                 { symbol: sym, risk_pct: 0.005 })
      .then(setRiskPrev)
      .catch(() => setRiskPrev(null))
      .finally(() => setRiskCalc(false));
  }, [sym]);

  const downloadCsv = () => {
    const rows = [["date", "open", "high", "low", "close", "volume"],
      ...bars.map((b) => [b.time, b.open, b.high, b.low, b.close, b.volume])];
    const url = URL.createObjectURL(new Blob(
      [rows.map((r) => r.join(",")).join("\n")],
      { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url; a.download = `${sym}-${tf}.csv`; a.click();
    URL.revokeObjectURL(url);
  };

  if (err) return (
    <div className="p-6"><ErrorState title={sym} detail={err} /></div>);

  const s = d?.signals;
  const pos = d?.position;
  const atr = s?.atr_pct;

  return (
    <div className="space-y-4 p-4 sm:p-6">
      {/* header — market overview strip */}
      <PageHeader
        title={`${sym}${d?.name ? ` — ${d.name}` : ""}`}
        meta={[d?.sector, d?.exchange, d?.asset_class,
               s ? `as of ${s.as_of}` : null]
              .filter(Boolean).join(" · ") || undefined}
        actions={
          <button onClick={() => setPyrOpen(true)}
            className="rounded-full bg-accent px-4 py-1.5 text-[12px] font-semibold text-[#0b0f1a] hover:brightness-110">
            Pyramid calculator
          </button>} />

      {s && (
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
          <SigCell l="Last" v={`$${fmtNum(s.close, 2)}`} />
          <SigCell l="1D" v={<Pct v={s.ret_1d} />} />
          <SigCell l="1W" v={<Pct v={s.ret_1w} />} />
          <SigCell l="1M" v={<Pct v={s.ret_1m} />} />
          <SigCell l="ATR14" v={s.atr_pct ? `${(s.atr_pct * 100).toFixed(2)}%` : "—"} />
          <SigCell l="Vol ×20d" v={s.vol_ratio ? `${fmtNum(s.vol_ratio, 2)}×` : "—"} />
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-3">
        {/* chart — TradingView widget */}
        <SectionCard className="xl:col-span-2" title="Chart — TradingView">
          <iframe key={sym} title={`${sym} TradingView`}
            src={`https://www.tradingview.com/widgetembed/?symbol=${encodeURIComponent(sym)}&interval=D&theme=dark&style=1&timezone=Etc%2FUTC&withdateranges=1&hide_side_toolbar=0&allow_symbol_change=0&studies=%5B%22RSI%40tv-basicstudies%22%2C%22ATR%40tv-basicstudies%22%5D`}
            className="h-[380px] w-full border-0" allow="fullscreen" />
        </SectionCard>

        {/* right rail — position + engine verdicts */}
        <div className="space-y-4">
          {pos && (
            <SectionCard title={`Held position · ${pos.source ?? "book"}`}>
              <div className="num grid grid-cols-2 gap-2 text-[12px] sm:grid-cols-4 xl:grid-cols-2">
                <div><div className="text-[9px] text-faint">QTY</div>{fmtNum(pos.quantity, 0)}</div>
                <div><div className="text-[9px] text-faint">AVG</div>${fmtNum(pos.avg_cost, 2)}</div>
                <div><div className="text-[9px] text-faint">VALUE</div>${fmtNum(pos.market_value)}</div>
                <div><div className="text-[9px] text-faint">UNREAL.</div>
                  <span className={(pos.unrealized ?? 0) >= 0 ? "text-pos" : "text-neg"}>
                    {pos.unrealized != null ? `${pos.unrealized >= 0 ? "+" : ""}$${fmtNum(pos.unrealized)}` : "—"}
                  </span></div>
              </div>
            </SectionCard>
          )}

          <SectionCard title="Engine verdicts">
            <div className="grid grid-cols-2 gap-2">
              <div className="glass-tile p-2.5">
                <div className="text-[9px] uppercase text-dim">Green Zone</div>
                {d?.screening
                  ? <div className="mt-1 flex items-baseline gap-1.5">
                      <span className="num font-bold">{d.screening.score}<span className="text-[10px] text-faint">/20</span></span>
                      <StatusBadge tone={tone(d.screening.verdict)}>{d.screening.verdict}</StatusBadge>
                    </div>
                  : <div className="mt-1 text-[10px] text-faint">no screen</div>}
              </div>
              <div className="glass-tile p-2.5">
                <div className="text-[9px] uppercase text-dim">Technical</div>
                {d?.technical
                  ? <StatusBadge tone={tone(d.technical.decision)}>{d.technical.decision}</StatusBadge>
                  : <div className="mt-1 text-[10px] text-faint">no data</div>}
              </div>
              <div className="glass-tile p-2.5">
                <div className="text-[9px] uppercase text-dim">Valuation</div>
                {d?.valuation && (d.valuation.sticker || d.valuation.fair)
                  ? <div className="num mt-1 text-[11px]">
                      {d.valuation.sticker && <div>sticker ${fmtNum(d.valuation.sticker, 2)}</div>}
                      {d.valuation.buy && <div className="text-pos">buy ≤ ${fmtNum(d.valuation.buy, 2)}</div>}
                    </div>
                  : <div className="mt-1 text-[10px] text-faint">no valuation</div>}
              </div>
              <div className="glass-tile p-2.5">
                <div className="text-[9px] uppercase text-dim">Committee</div>
                {d?.decision
                  ? <StatusBadge tone={tone(d.decision.verdict)}>{d.decision.verdict}</StatusBadge>
                  : <div className="mt-1 text-[10px] text-faint">no decision</div>}
              </div>
              {/* Phase-1 qualification gate — spans the grid */}
              <div className="glass-tile col-span-2 p-2.5">
                <div className="flex items-center justify-between">
                  <div className="text-[9px] uppercase text-dim">Qualification gate</div>
                  {d?.qualification?.mos_price && (
                    <span className="num text-[9px] text-faint">
                      MOS ${fmtNum(d.qualification.mos_price, 2)}
                    </span>
                  )}
                </div>
                {d?.qualification
                  ? <div className="mt-1 flex items-center gap-1.5">
                      <StatusBadge tone={tone(d.qualification.verdict)}>
                        {d.qualification.verdict}
                      </StatusBadge>
                      <span className="text-[9px] text-faint">
                        4M {["meaning","moat","management"].map(k =>
                          d.qualification.four_ms?.[k] === true ? "✓" : "·").join("")}
                        {" "}· 5-num {d.qualification.five_numbers_pass ? "✓" : "·"}
                        {d.qualification.rule1_zone
                          ? ` · ${d.qualification.rule1_zone}` : ""}
                      </span>
                    </div>
                  : <div className="mt-1 text-[10px] text-faint">no gate data</div>}
              </div>
            </div>
            {d?.dossier && (
              <a href={`/research?symbol=${sym}`}
                className="mt-2 block rounded-lg border border-border px-3 py-1.5 text-center text-[11px] text-dim hover:border-accent/40 hover:text-text">
                Research dossier v{d.dossier.version} ({d.dossier.sections} sections) →
              </a>
            )}
          </SectionCard>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        {/* market intelligence — full signal surface */}
        <SectionCard title="Market intelligence" className="xl:col-span-1"
          action={s ? `as of ${s.as_of}` : undefined}>
          {s ? (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              <MiGroup title="Trend" rows={[
                ["SMA50", s.above_sma50 == null ? "—"
                  : s.above_sma50 ? "above" : "below"],
                ["SMA200", s.above_sma200 == null ? "—"
                  : s.above_sma200 ? "above" : "below"],
                ["Regime", s.regime ?? "—"],
              ]} />
              <MiGroup title="Momentum" rows={[
                ["RSI-14", s.rsi14 != null ? fmtNum(s.rsi14, 1) : "—"],
                ["Will %R", s.williams_r != null ? fmtNum(s.williams_r, 1) : "—"],
                ["MACD", s.macd
                  ? `${fmtNum(s.macd.macd, 2)}/${fmtNum(s.macd.signal, 2)}`
                  : "—"],
                ["1W ret", <Pct key="r" v={s.ret_1w} />],
              ]} />
              <MiGroup title="Volatility" rows={[
                ["ATR", s.atr_abs != null ? `$${fmtNum(s.atr_abs, 2)}` : "—"],
                ["ATR %", s.atr_pct != null
                  ? `${(s.atr_pct * 100).toFixed(2)}%` : "—"],
                ["ADX-14", s.adx14 != null ? fmtNum(s.adx14, 1) : "—"],
                ["CMI-21", s.cmi != null ? fmtNum(s.cmi, 1) : "—"],
              ]} />
              <MiGroup title="Structure" rows={[
                ["52w high", <Pct key="h" v={s.from_52w_high} />],
                ["52w low", <Pct key="l" v={s.from_52w_low} />],
                ["20d hi", s.hi_20 != null ? `$${fmtNum(s.hi_20, 2)}` : "—"],
                ["20d lo", s.lo_20 != null ? `$${fmtNum(s.lo_20, 2)}` : "—"],
              ]} />
              <MiGroup title="Volume / returns" rows={[
                ["Vol ratio", s.vol_ratio != null
                  ? `${fmtNum(s.vol_ratio, 2)}×` : "—"],
                ["1D", <Pct key="d" v={s.ret_1d} />],
                ["1M", <Pct key="m" v={s.ret_1m} />],
              ]} />
            </div>
          ) : <EmptyState title="No signal data" hint="needs ≥30 daily bars" />}
        </SectionCard>

        {/* risk & pyramid */}
        <SectionCard title="Risk & pyramid" className="xl:col-span-1"
          action={
            <button onClick={() => setPyrOpen(true)}
              className="rounded-full border border-accent/50 px-3 py-0.5 text-[11px] text-accent hover:bg-accent/10">
              Calculator
            </button>}>
          {s?.atr_pct ? (
            <div className="space-y-1.5 text-[12px]">
              {/* Step 9 — trade-eligibility verdict + Step-11 margin */}
              <div className="flex flex-wrap items-center gap-2">
                {riskCalc && !riskPrev ? (
                  <span className="flex items-center gap-1.5 text-[10px] text-faint">
                    <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-border border-t-accent" />
                    calculating…
                  </span>
                ) : riskPrev?.eligibility ? (
                  <StatusBadge tone={
                    riskPrev.eligibility.verdict === "TRADE_ELIGIBLE"
                      ? "pos" : "neg"}>
                    {riskPrev.eligibility.verdict?.replaceAll("_", " ")}
                  </StatusBadge>
                ) : null}
                {riskPrev?.sleeve?.state?.margin_call_distance != null && (
                  <span className="num text-[10px] text-faint">
                    margin-call distance $
                    {fmtNum(riskPrev.sleeve.state.margin_call_distance, 0)}
                  </span>
                )}
              </div>
              {/* Step 10–12 — daily Trade Risk Sheet numbers */}
              {(() => {
                const sheet = riskPrev?.sheets?.["1d"];
                return sheet?.shares != null ? (
                  <div className="num grid grid-cols-3 gap-2">
                    <SigCell l="Stop −1.5×ATR"
                      v={`$${fmtNum(sheet.stop, 2)}`} />
                    <SigCell l="Tgt +3×ATR"
                      v={`$${fmtNum(sheet.target, 2)}`} />
                    <SigCell l="R:R"
                      v={sheet.rr != null ? `${fmtNum(sheet.rr, 1)}:1` : "—"} />
                    <SigCell l={`Shares${sheet.binding
                      ? ` · ${sheet.binding}` : ""}`}
                      v={fmtNum(sheet.shares, 0)} />
                    <SigCell l="$ Risk"
                      v={sheet.dollar_risk != null
                        ? `$${fmtNum(sheet.dollar_risk, 0)}` : "—"} />
                    <SigCell l="Margin"
                      v={sheet.margin_required != null
                        ? `$${fmtNum(sheet.margin_required, 0)}` : "—"} />
                  </div>
                ) : (
                  <div className="num grid grid-cols-3 gap-2">
                    <SigCell l="ATR14" v={`${(s.atr_pct * 100).toFixed(2)}%`} />
                    <SigCell l="Stop −1.5×" v={s.close ? `$${fmtNum(s.close * (1 - 1.5 * s.atr_pct), 2)}` : "—"} />
                    <SigCell l="Tgt +3×" v={s.close ? `$${fmtNum(s.close * (1 + 3 * s.atr_pct), 2)}` : "—"} />
                  </div>
                );
              })()}
              {d?.pyramids?.length ? (
                <div className="space-y-1 pt-1">
                  {d.pyramids.map((p: any) => (
                    <div key={p.id}
                      className="flex items-center justify-between rounded-lg border border-border px-2.5 py-1.5 text-[11px]">
                      <StatusBadge tone={p.state === "trade_eligible" ? "warn" : "pos"}>{p.state}</StatusBadge>
                      <span className="num">entry ${fmtNum(p.entry, 2)}</span>
                      <span className="num text-neg">stop ${fmtNum(p.stop, 2)}</span>
                      <span className="num text-pos">tgt ${fmtNum(p.target1, 2)}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="pt-1 text-[11px] text-faint">
                  no open pyramid — size one in the calculator
                </div>
              )}
            </div>
          ) : <EmptyState title="No ATR" hint="needs daily bars" />}
        </SectionCard>

      </div>

      {/* history + financials side-by-side on xl — the statement
          tables scroll internally instead of stacking the page tall */}
      <div className="grid gap-4 xl:grid-cols-2">
      <SectionCard title="Stock Price History"
        action={
          <div className="flex items-center gap-2">
            {barsMeta?.stale && (
              <StatusBadge tone="warn">
                stale · {barsMeta.sessions_behind ?? "?"} session
                {barsMeta.sessions_behind === 1 ? "" : "s"} behind
              </StatusBadge>
            )}
            {barsMeta?.as_of && (
              <span className="num text-[10px] text-faint">
                as of {barsMeta.as_of}
                {barsMeta.refreshed ? " · refreshed" : ""}
              </span>
            )}
            <select value={tf} onChange={(e) => setTf(e.target.value)}
              className="rounded border border-border bg-surface-2 px-2 py-0.5 text-[11px] text-dim focus:border-accent focus:outline-none">
              {TFS.map((t) => <option key={t.v} value={t.v}>{t.label}</option>)}
            </select>
            <button onClick={downloadCsv} disabled={!bars.length}
              className="rounded-full border border-border px-3 py-0.5 text-[11px] text-dim hover:text-text disabled:opacity-40">
              Download CSV
            </button>
          </div>}>
        {barsErr && (
          <div className="mb-2 rounded-lg border border-neg/30 bg-neg/5 px-3 py-1.5 text-[11px] text-neg">
            {barsErr}
          </div>
        )}
        {bars.length === 0 ? (
          <EmptyState title="No stored bars"
            hint="Daily bars ingest via the dataops backfill." />
        ) : (
          <div className="max-h-80 overflow-y-auto">
            <table className="w-full text-[12px]">
              <thead className="sticky top-0 bg-[#0b0f1a]">
                <tr className="border-b border-border text-left text-[10px] uppercase tracking-wide text-faint">
                  <th className="py-1.5 pr-2">Date</th>
                  <th className="py-1.5 pr-2 text-right">Price</th>
                  <th className="py-1.5 pr-2 text-right">Open</th>
                  <th className="py-1.5 pr-2 text-right">High</th>
                  <th className="py-1.5 pr-2 text-right">Low</th>
                  <th className="py-1.5 pr-2 text-right">Vol.</th>
                  <th className="py-1.5 text-right">Change %</th>
                </tr>
              </thead>
              <tbody className="num">
                {[...bars].reverse().map((b, i, arr) => {
                  const prevC = arr[i + 1]?.close;
                  const chg = prevC ? b.close / prevC - 1 : null;
                  return (
                    <tr key={b.time}
                      className="border-b border-border/40 last:border-0 hover:bg-surface-2/50">
                      <td className="py-1.5 pr-2 text-dim">{b.time}{b.provisional ? " *" : ""}</td>
                      <td className={`py-1.5 pr-2 text-right font-semibold ${chg == null ? "" : chg >= 0 ? "text-pos" : "text-neg"}`}>
                        {fmtNum(b.close, 2)}</td>
                      <td className="py-1.5 pr-2 text-right">{fmtNum(b.open, 2)}</td>
                      <td className="py-1.5 pr-2 text-right">{fmtNum(b.high, 2)}</td>
                      <td className="py-1.5 pr-2 text-right">{fmtNum(b.low, 2)}</td>
                      <td className="py-1.5 pr-2 text-right text-dim">
                        {b.provisional ? "—" : b.volume >= 1e6 ? `${fmtNum(b.volume / 1e6, 2)}M` : fmtNum(b.volume / 1e3, 0) + "K"}</td>
                      <td className={`py-1.5 text-right ${chg == null ? "text-faint" : chg >= 0 ? "text-pos" : "text-neg"}`}>
                        {chg == null ? "—" : `${chg >= 0 ? "+" : ""}${(chg * 100).toFixed(2)}%`}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      {/* financials — investing.com layout: key ratios + statements */}
      <SectionCard title="Financials"
        action={d?.fundamentals ? `${d.fundamentals.facts} concepts · SEC filings` : undefined}>
        {!d?.fundamentals?.facts ? (
          <EmptyState title="No fundamentals"
            hint="SEC facts ingest via ingest:edgar:facts:{SYM}" />
        ) : (() => {
          const f = d.fundamentals;
          const r = f.ratios ?? {};
          const fmtBig = (v: number | null, unit?: string | null) =>
            v == null ? "—" : unit === "USD" || unit === "shares"
              ? fmtNum(v / 1e6, Math.abs(v) >= 1e8 ? 0 : 2) + "M"
              : fmtNum(v, 2);
          const cols = (f.periods ?? []).filter((p: any) =>
            finFreq === "FY" ? p.fp === "FY" : p.fp.startsWith("Q"));
          const rows = (f.statements?.[finTab] ?? []).map((c: any) => ({
            ...c,
            pts: Object.fromEntries(
              (c.points ?? []).filter((p: any) =>
                cols.some((col: any) => col.end === p.end))
                .map((p: any) => [p.end, p.value])),
          }));
          return (
            <div className="space-y-3">
              {/* key ratios */}
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {[
                  ["P/E", r.pe ? fmtNum(r.pe, 1) : "—"],
                  ["P/B", r.pb ? fmtNum(r.pb, 2) : "—"],
                  ["Debt/Eq", r.debt_equity != null ? `${(r.debt_equity * 100).toFixed(0)}%` : "—"],
                  ["ROE", r.roe != null ? `${(r.roe * 100).toFixed(2)}%` : "—"],
                  ["Div Yield", r.dividend_yield ? `${(r.dividend_yield * 100).toFixed(2)}%` : "—"],
                  ["EBITDA", r.ebitda != null ? `${fmtNum(r.ebitda / 1e9, 2)}B` : "—"],
                  ["EPS dil.", r.eps_diluted != null ? fmtNum(r.eps_diluted, 2) : "—"],
                  ["Shares", r.shares ? `${fmtNum(r.shares / 1e9, 2)}B` : "—"],
                ].map(([l, v]) => (
                  <div key={l as string} className="glass-tile px-2.5 py-1.5">
                    <div className="text-[9px] uppercase tracking-wide text-faint">{l}</div>
                    <div className="num mt-0.5 text-[13px] font-semibold">{v}</div>
                  </div>
                ))}
              </div>
              {/* statement tabs + frequency */}
              <div className="flex flex-wrap items-center gap-2">
                <div className="flex gap-1">
                  {([["income", "Income Statement"], ["balance", "Balance Sheet"],
                     ["cashflow", "Cash Flow"], ["other", "Other"]] as const)
                    .map(([k, l]) => (
                      <button key={k} onClick={() => setFinTab(k)}
                        className={`rounded-full px-3 py-1 text-[11px] transition ${
                          finTab === k ? "bg-accent text-[#0b0f1a] font-semibold"
                            : "border border-border text-dim hover:text-text"}`}>
                        {l}
                      </button>
                    ))}
                </div>
                <div className="ml-auto flex gap-1">
                  {(["FY", "Q"] as const).map((fq) => (
                    <button key={fq} onClick={() => setFinFreq(fq)}
                      className={`rounded-full px-3 py-1 text-[11px] transition ${
                        finFreq === fq ? "bg-surface-3 text-accent font-semibold"
                          : "border border-border text-dim hover:text-text"}`}>
                      {fq === "FY" ? "Annual" : "Quarterly"}
                    </button>
                  ))}
                </div>
              </div>
              {/* periods-as-columns statement table */}
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] text-[12px]">
                  <thead>
                    <tr className="border-b border-border text-[10px] uppercase tracking-wide text-faint">
                      <th className="py-1.5 pr-2 text-left">Period ending</th>
                      {cols.map((c: any) => (
                        <th key={c.end} className="py-1.5 text-right">
                          {c.end.slice(0, 7)}
                          <span className="ml-1 text-[9px]">{c.fp}</span>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="num">
                    {rows.filter((r2: any) => Object.keys(r2.pts).length).map((r2: any) => (
                      <tr key={r2.concept}
                        className="border-b border-border/40 last:border-0">
                        <td className="py-1.5 pr-2 text-dim">
                          {r2.label.replace(/([a-z])([A-Z])/g, "$1 $2")}</td>
                        {cols.map((c: any) => (
                          <td key={c.end} className="py-1.5 text-right">
                            {r2.pts[c.end] != null ? fmtBig(r2.pts[c.end], r2.unit) : "—"}
                          </td>
                        ))}
                      </tr>
                    ))}
                    {rows.filter((r2: any) => Object.keys(r2.pts).length).length === 0 && (
                      <tr><td colSpan={cols.length + 1}
                        className="py-3 text-center text-[11px] text-faint">
                        no {finFreq === "FY" ? "annual" : "quarterly"} facts on this statement
                      </td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          );
        })()}
      </SectionCard>
      </div>

      {/* news */}
      <SectionCard title="News"
        action={news?.sources?.length ? news.sources.join(" + ") : undefined}>
        {!news ? (
          <div className="text-[12px] text-faint">loading…</div>
        ) : news.items.length === 0 ? (
          <EmptyState title="No news"
            hint="configure ALPACA_API_KEY for headlines; SEC filings appear when the instrument has a CIK." />
        ) : (
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {news.items.map((n, i) => (
              <a key={i} href={n.url ?? undefined} target="_blank" rel="noreferrer"
                className="glass-tile block px-3 py-2 transition hover:border-accent/40">
                <div className="flex items-center justify-between text-[10px] text-faint">
                  <span className="uppercase tracking-wider">{n.source}</span>
                  <span className="num">{n.at ? fmtTime(n.at) : ""}</span>
                </div>
                <div className="mt-0.5 text-[12px] font-medium leading-snug">{n.title}</div>
                {n.summary && <div className="mt-0.5 line-clamp-2 text-[11px] text-dim">{n.summary}</div>}
              </a>
            ))}
          </div>
        )}
      </SectionCard>

      <PyramidModal open={pyrOpen} onClose={() => setPyrOpen(false)}
        initial={sym} onCreated={() => {
          apiGet(`/api/v1/symbol/${sym}`).then(setD).catch(() => {});
        }} />
    </div>
  );
}
