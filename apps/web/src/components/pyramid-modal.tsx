"use client";

import { useEffect, useRef, useState } from "react";

import { apiGet, apiPost } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { Dialog } from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { SearchInput } from "@/components/ui/search-input";
import { StatusBadge } from "@/components/ui/status-badge";

interface Hit { id: string; symbol: string; name: string;
  asset_class: string; research_eligible: boolean }

const DAY_LABELS: Record<string, string> = {
  "6": "~1wk", "24": "~1mo", "72": "~3mo", "288": "~1y", "576": "~2y" };
const WK_LABELS: Record<string, string> = {
  "12": "~3mo", "26": "~6mo", "52": "~1y", "104": "~2y", "156": "~3y" };
const MO_LABELS: Record<string, string> = {
  "6": "~6mo", "12": "~1y", "24": "~2y", "36": "~3y", "60": "~5y" };

/** multi-day frames report window counts in n-session buckets —
 * remap keys to the session-equivalent daily labels (~6d, ~24d…) */
const subLabels = (n: number): Record<string, string> =>
  Object.fromEntries(Object.entries(DAY_LABELS)
    .map(([k, v]) => [String(Number(k) / n), v]));

const FRAMES = [
  { tf: "1d", label: "Daily" }, { tf: "2d", label: "2-Day" },
  { tf: "3d", label: "3-Day" }, { tf: "1w", label: "Weekly" },
  { tf: "1mo", label: "Monthly" },
] as const;
type Frame = (typeof FRAMES)[number]["tf"];

const HIST_TFS = [
  { v: "1d", label: "Daily" }, { v: "2d", label: "2-Day" },
  { v: "3d", label: "3-Day" }, { v: "1w", label: "Weekly" },
  { v: "1mo", label: "Monthly" }, { v: "1y", label: "Yearly" },
];

function pct(v: number | null | undefined, d = 2) {
  return v == null ? "—" : `${(v * 100).toFixed(d)}%`;
}

/** One timeframe table from the Excel "ATR Output" sheet — the
 * workbook windows are fixed (6d / 12w / 6m = first horizon column);
 * the period renders as a label, not an input. Optional `sub` rows
 * show the same formula run on n-session price buckets. */
function AtrTable({ title, unit, data, labels, sub }:
  { title: string; unit: string; data: any;
    labels: Record<string, string>;
    sub?: { k: string; data: any }[] }) {
  const wins = Object.entries(data?.windows ?? {});
  return (
    <div className="glass-tile p-3">
      <div className="flex items-center justify-between">
        <div className="text-[10px] uppercase tracking-wide text-dim">{title}</div>
        <span className="text-[9px] text-faint">
          SMA{data?.period ?? "–"}{unit}
        </span>
      </div>
      <div className="num mt-1 text-lg font-semibold">
        {pct(data?.atr_pct)}
        <span className="ml-2 text-[11px] font-normal text-faint">
          ${fmtNum(data?.atr_abs, 2)} abs
        </span>
      </div>
      {wins.length > 0 && (
        <div className="num mt-2 grid grid-cols-3 gap-1 text-[11px]">
          {wins.map(([w, v]) => (
            <div key={w}>
              <span className="text-faint">{w}{unit} </span>
              {pct(v as number, 2)}
              <span className="ml-1 text-[9px] text-faint">{labels[w]}</span>
            </div>
          ))}
        </div>
      )}
      {sub?.some((s) => s.data?.atr_pct != null) && (
        <div className="num mt-2 flex flex-wrap gap-x-4 gap-y-0.5 border-t border-border/50 pt-2 text-[11px]">
          {sub.map((s) => (
            <div key={s.k}>
              <span className="uppercase text-faint">{s.k} ATR </span>
              {pct(s.data?.atr_pct)}
              <span className="ml-1 text-[9px] text-faint">
                ${fmtNum(s.data?.atr_abs, 2)}
                {s.data?.window_sessions != null &&
                  ` · ${s.data.window_sessions}s win`}
              </span>
            </div>
          ))}
        </div>
      )}
      <div className="mt-1 text-[9px] text-faint">{data?.bars ?? 0} bars · SMA{data?.period ?? "?"}(TR)</div>
    </div>
  );
}

/** Compact price history — same bars endpoint the symbol page uses,
 * with the 2d/3d session-bucket timeframes the ATR cards read. */
function PriceHistory({ sym }: { sym: string }) {
  const [tf, setTf] = useState("1d");
  const [bars, setBars] = useState<any[]>([]);
  const [meta, setMeta] = useState<any | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let live = true;
    setErr(null); setLoading(true);
    apiGet<any>(`/api/v1/technical/bars/${sym}?timeframe=${tf}&limit=400`)
      .then((r) => { if (live) { setBars(r.bars); setMeta(r); } })
      .catch((e) => { if (live) { setBars([]); setErr(e.message); } })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [sym, tf]);

  return (
    <div className="glass-tile flex h-full flex-col p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[10px] uppercase tracking-wide text-dim">
          Price history
        </span>
        <div className="flex items-center gap-2">
          {meta?.stale && (
            <StatusBadge tone="warn">
              stale · {meta.sessions_behind ?? "?"}s
            </StatusBadge>)}
          <select value={tf} onChange={(e) => setTf(e.target.value)}
            className="rounded border border-border bg-surface-2 px-1.5 py-0.5 text-[10px] text-dim focus:border-accent focus:outline-none">
            {HIST_TFS.map((t) =>
              <option key={t.v} value={t.v}>{t.label}</option>)}
          </select>
        </div>
      </div>
      {err && <div className="mb-1 text-[11px] text-neg">{err}</div>}
      {loading && bars.length === 0 ? (
        <div className="flex items-center justify-center gap-2 py-8 text-[12px] text-faint">
          <span className="size-3 animate-spin rounded-full border border-faint/40 border-t-accent" />
          loading…
        </div>
      ) : bars.length === 0 ? (
        <div className="py-4 text-[11px] text-faint">No stored bars</div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <table className="w-full text-[11px]">
            <thead className="sticky top-0 bg-[#0b0f1a]">
              <tr className="border-b border-border text-left text-[9px] uppercase tracking-wide text-faint">
                <th className="py-1 pr-2">Date</th>
                <th className="py-1 pr-2 text-right">Price</th>
                <th className="py-1 pr-2 text-right">Open</th>
                <th className="py-1 pr-2 text-right">High</th>
                <th className="py-1 pr-2 text-right">Low</th>
                <th className="py-1 pr-2 text-right">Vol.</th>
                <th className="py-1 text-right">Chg%</th>
              </tr>
            </thead>
            <tbody className="num">
              {[...bars].reverse().map((b, i, arr) => {
                const prevC = arr[i + 1]?.close;
                const chg = prevC ? b.close / prevC - 1 : null;
                return (
                  <tr key={b.time}
                    className="border-b border-border/40 last:border-0 hover:bg-surface-2/50">
                    <td className="py-1 pr-2 text-dim">{b.time}{b.provisional ? " *" : ""}</td>
                    <td className={`py-1 pr-2 text-right font-semibold ${chg == null ? "" : chg >= 0 ? "text-pos" : "text-neg"}`}>
                      {fmtNum(b.close, 2)}</td>
                    <td className="py-1 pr-2 text-right">{fmtNum(b.open, 2)}</td>
                    <td className="py-1 pr-2 text-right">{fmtNum(b.high, 2)}</td>
                    <td className="py-1 pr-2 text-right">{fmtNum(b.low, 2)}</td>
                    <td className="py-1 pr-2 text-right text-dim">
                      {b.provisional ? "—"
                        : b.volume >= 1e6 ? `${fmtNum(b.volume / 1e6, 2)}M`
                        : `${fmtNum(b.volume / 1e3, 0)}K`}</td>
                    <td className={`py-1 text-right ${chg == null ? "text-faint" : chg >= 0 ? "text-pos" : "text-neg"}`}>
                      {chg == null ? "—" : `${chg >= 0 ? "+" : ""}${(chg * 100).toFixed(2)}%`}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SheetRow({ label, value, tone }:
  { label: string; value: string; tone?: string }) {
  return (
    <div className="flex items-center justify-between border-b border-border/50 py-1 last:border-0">
      <span className="text-[11px] text-dim">{label}</span>
      <span className={`num text-[12px] font-medium ${tone ?? ""}`}>{value}</span>
    </div>
  );
}

export function PyramidModal({ open, onClose, initial, onCreated }:
  { open: boolean; onClose: () => void;
    initial?: string; onCreated: () => void }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [sym, setSym] = useState<string | null>(null);
  const [name, setName] = useState<string | null>(null);
  const [atrRep, setAtrRep] = useState<any | null>(null);
  const [prev, setPrev] = useState<any | null>(null);
  const [frame, setFrame] = useState<Frame>("1d");
  const [entry, setEntry] = useState("");
  const [equity, setEquity] = useState("");
  const [atrIn, setAtrIn] = useState("");          // hypothetical ATR $
  const [leverage, setLeverage] = useState(5);     // sleeve gross lever
  const [tvOpen, setTvOpen] = useState(false);
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false);
  const [calc, setCalc] = useState(false);   // a calculation is in flight
  const [err, setErr] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const debRef = useRef<ReturnType<typeof setTimeout>>(null);
  const atrRef = useRef<ReturnType<typeof setTimeout>>(null);

  const sleeveOn = !!prev?.sleeve?.enabled;

  // pre-seed with the charted symbol
  useEffect(() => {
    if (open && initial && !loaded) selectSym(initial);
    if (!open) { setLoaded(false); setErr(null); setTvOpen(false); }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  // debounced ticker search
  useEffect(() => {
    if (debRef.current) clearTimeout(debRef.current);
    if (!q.trim()) { setHits([]); return; }
    debRef.current = setTimeout(() => {
      apiGet<Hit[]>(`/api/v1/universe/instruments?q=${encodeURIComponent(q.trim())}&limit=8`)
        .then(setHits).catch(() => setHits([]));
    }, 250);
    return () => { if (debRef.current) clearTimeout(debRef.current); };
  }, [q]);

  // sheet inputs changed → the whole sheet (sleeve, eligibility,
  // risk sheet) refreshes itself — no manual trigger needed
  useEffect(() => {
    if (!sym || !atrRep || atrRep.insufficient) return;
    // skip the echo of our own autofill — only real edits recompute
    if (prev
        && parseFloat(entry || "0") === prev.inputs.entry
        && parseFloat(equity || "0") === Math.round(prev.inputs.equity)
        && (leverage === (prev.inputs.leverage
                          ?? prev.sleeve?.config?.target_leverage))
        && ((parseFloat(atrIn) || null) ===
            (prev.inputs.atr_override ?? null))) return;
    if (atrRef.current) clearTimeout(atrRef.current);
    atrRef.current = setTimeout(() => recalc(), 450);
    return () => { if (atrRef.current) clearTimeout(atrRef.current); };
  }, [entry, equity, leverage, atrIn, frame]); // eslint-disable-line react-hooks/exhaustive-deps

  async function selectSym(s: string, hitName?: string) {
    const up = s.toUpperCase();
    setSym(up); setName(hitName ?? null);
    setQ(""); setHits([]);
    setErr(null); setPrev(null); setAtrRep(null); setForce(false);
    setEntry("");        // ← stale entry poisoned the next symbol's
    setAtrIn("");        //    sheet (AMD's price sized NVDA's trade)
    setLoaded(true);
    setCalc(true);
    try {
      // workbook windows are the server defaults — 6d / 12w / 6m
      const a = await apiGet<any>(`/api/v1/risk/atr/${up}`);
      setAtrRep(a);
      if (a?.name) setName(a.name);
      if (a?.insufficient) {
        setErr(a.note ?? "insufficient bars");
        return;
      }
      await recalc();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "no ATR data for symbol");
    } finally { setCalc(false); }
  }

  async function recalc() {
    if (!sym) return;
    setCalc(true);
    try {
      const body: any = {
        symbol: sym,
        risk_pct: 0.005,
        timeframe: frame,
      };
      if (entry) body.entry = parseFloat(entry);
      if (equity) body.equity = parseFloat(equity);
      body.leverage = leverage;
      if (atrIn) body.atr_override = parseFloat(atrIn);
      const p = await apiPost<any>("/api/v1/risk/pyramid/preview", body);
      setPrev(p);
      if (!entry) setEntry(String(p.inputs.entry));
      if (!equity) setEquity(String(Math.round(p.inputs.equity)));
    } catch (e) {
      setPrev(null);
      setErr(e instanceof Error ? e.message : "preview failed");
    } finally { setCalc(false); }
  }

  async function create() {
    if (!prev || !sym) return;
    setBusy(true); setErr(null);
    try {
      await apiPost("/api/v1/risk/pyramid", {
        symbol: sym,
        entry: prev.inputs.entry,
        atr: prev.atr.abs,
        equity: prev.inputs.equity,
        cash: prev.inputs.cash,
        risk_pct: prev.inputs.risk_pct,
        force,
      });
      onCreated();
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "create failed");
    } finally { setBusy(false); }
  }

  // ATR card data for the selected frame — 2d/3d ride inside the
  // daily report's sub-frames (same formula, n-session buckets)
  const frameAtr = (tf: Frame) =>
    tf === "1d" ? atrRep?.daily
    : tf === "2d" ? atrRep?.daily?.sub?.["2d"]
    : tf === "3d" ? atrRep?.daily?.sub?.["3d"]
    : tf === "1w" ? atrRep?.weekly : atrRep?.monthly;
  const frameLabels = (tf: Frame) =>
    tf === "1d" ? DAY_LABELS : tf === "2d" ? subLabels(2)
    : tf === "3d" ? subLabels(3)
    : tf === "1w" ? WK_LABELS : MO_LABELS;
  const frameUnit = (tf: Frame) =>
    tf === "2d" ? "×2d" : tf === "3d" ? "×3d"
    : tf === "1w" ? "w" : tf === "1mo" ? "m" : "d";
  const frameTitle = FRAMES.find((f) => f.tf === frame)!.label;
  const sh = prev?.sheets?.[frame]
    ?? (frame === "1d" ? prev?.sheet : null);

  return (
    <Dialog open={open} onClose={onClose} size="2xl"
      title="Pyramid Calculator — ATR Output & Trade Risk Sheet">
      {/* ticker search */}
      <div className="relative">
        <SearchInput value={q} onChange={setQ}
          placeholder="Search ticker — AAPL, NVDA, XLK…" />
        {hits.length > 0 && (
          <div className="absolute z-10 mt-1 w-full rounded border border-border bg-[#131a2b] shadow-xl">
            {hits.map((h) => (
              <button key={h.id} onClick={() => selectSym(h.symbol, h.name)}
                className="flex w-full items-center justify-between px-3 py-1.5 text-left text-[12px] hover:bg-surface-2">
                <span><b className="num">{h.symbol}</b>
                  <span className="ml-2 text-dim">{h.name}</span></span>
                <span className="text-[10px] text-faint">{h.asset_class}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* selected ticker — what you are analyzing */}
      {sym && (
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-accent/30 bg-accent/5 px-3 py-2">
          <span className="text-[20px] font-bold text-accent">{sym}</span>
          {name && <span className="text-[13px] text-dim">{name}</span>}
          {atrRep?.close != null &&
            <span className="num text-[13px] font-semibold">${fmtNum(atrRep.close, 2)}</span>}
          {prev?.vol_regime &&
            <StatusBadge tone={prev.vol_regime === "expanding" ? "warn" : "info"}>
              {prev.vol_regime}
            </StatusBadge>}
          {calc && (
            <span className="flex items-center gap-1.5 text-[11px] text-dim">
              <span className="size-2.5 animate-spin rounded-full border border-faint/40 border-t-accent" />
              calculating…
            </span>)}
          <button onClick={() => setTvOpen((v) => !v)}
            className={`ml-auto rounded-full px-3 py-1 text-[11px] font-medium transition ${
              tvOpen ? "bg-accent text-[#0b0f1a]" : "border border-border text-dim hover:text-text"}`}>
            {tvOpen ? "Hide chart" : "TradingView"}
          </button>
        </div>
      )}

      {/* TradingView embed */}
      {sym && tvOpen && (
        <div className="mt-2 overflow-hidden rounded-lg border border-border">
          <iframe key={sym} title={`${sym} TradingView`}
            src={`https://www.tradingview.com/widgetembed/?symbol=${encodeURIComponent(sym)}&interval=D&theme=dark&style=1&timezone=Etc%2FUTC&withdateranges=1&hide_side_toolbar=0&allow_symbol_change=0&studies=%5B%22ATR%40tv-basicstudies%22%5D`}
            className="h-[340px] w-full border-0" allow="fullscreen" />
        </div>
      )}

      {err && <div className="mt-2 text-[12px] text-warn">{err}</div>}

      {!sym && (
        <div className="mt-4">
          <EmptyState title="Search a ticker"
            hint="Runs the docs' ATR Output sheet + Trade Risk Sheet on real stored bars — SMA14(TR), 1.5×ATR stop, 3×ATR target." />
        </div>
      )}

      {sym && atrRep?.insufficient && (
        <div className="mt-4">
          <EmptyState title={`${sym}: insufficient bars`}
            hint={atrRep.note ?? "needs ≥8 daily bars for the workbook ATR"} />
        </div>
      )}

      {sym && atrRep && !atrRep.insufficient && (
        <div className="mt-3 grid gap-3 xl:grid-cols-2">
          {/* ── left — calculator column: inputs → ATR → sleeve →
              eligibility → selected-frame risk sheet ── */}
          <div className="space-y-3">
            {/* inputs — Entry / Equity / Leverage drive the whole
                sheet; hypothetical ATR $ is optional what-if */}
            <div className="glass-tile p-3">
              <div className="flex flex-wrap items-end gap-3">
                <label className="w-24">
                  <span className="text-[10px] uppercase text-dim">Entry</span>
                  <input value={entry}
                    onChange={(e) => setEntry(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && recalc()}
                    className="num mt-0.5 w-full rounded border border-border bg-surface-2 px-2 py-1 text-[12px] focus:border-accent focus:outline-none" />
                  <span className="text-[9px] text-faint">
                    ${fmtNum(atrRep.close, 2)} last</span>
                </label>
                <label className="w-28">
                  <span className="text-[10px] uppercase text-dim">Equity $</span>
                  <input value={equity}
                    onChange={(e) => setEquity(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && recalc()}
                    className="num mt-0.5 w-full rounded border border-border bg-surface-2 px-2 py-1 text-[12px] focus:border-accent focus:outline-none" />
                  <span className="text-[9px] text-faint">live NAV</span>
                </label>
                <label className="w-24">
                  <span className="text-[10px] uppercase text-dim">
                    ATR $ <span className="normal-case text-faint">opt</span></span>
                  <input value={atrIn}
                    onChange={(e) => setAtrIn(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && recalc()}
                    placeholder={fmtNum(frameAtr(frame)?.atr_abs, 2)}
                    className="num mt-0.5 w-full rounded border border-border bg-surface-2 px-2 py-1 text-[12px] focus:border-accent focus:outline-none" />
                  <span className="text-[9px] text-faint">hypothetical</span>
                </label>
                <label className="min-w-44 flex-1">
                  <span className="flex items-center justify-between text-[10px] uppercase text-dim">
                    Leverage
                    <b className="num text-accent">{leverage}×</b>
                  </span>
                  <input type="range" min={1} max={100} step={1}
                    value={leverage}
                    disabled={prev != null && !sleeveOn}
                    onChange={(e) => setLeverage(Number(e.target.value))}
                    className="mt-1.5 w-full accent-[#f0b429] disabled:opacity-40" />
                  <span className="text-[9px] text-faint">
                    {prev == null ? "sleeve gross cap"
                      : sleeveOn ? "sleeve gross cap ×equity"
                      : "sleeve off — flat sizing"}</span>
                </label>
                <button onClick={() => recalc()} disabled={calc}
                  className="rounded bg-accent px-3 py-1.5 text-[12px] font-semibold text-[#0b0f1a] hover:brightness-110 disabled:opacity-60">
                  {calc ? "…" : "Recalculate"}
                </button>
              </div>
            </div>

            {/* ATR Output — one frame at a time, switched inline */}
            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <span className="text-[10px] uppercase text-dim">
                  ATR Output — {frameTitle} frame
                </span>
                <div className="flex rounded-lg border border-border p-0.5">
                  {FRAMES.map((f) => (
                    <button key={f.tf} onClick={() => setFrame(f.tf)}
                      className={`rounded-md px-2 py-0.5 text-[10px] transition ${
                        frame === f.tf
                          ? "bg-accent font-semibold text-[#0b0f1a]"
                          : "text-dim hover:text-text"}`}>
                      {f.label}
                    </button>
                  ))}
                </div>
              </div>
              <AtrTable title={`${frameTitle} ATR`}
                unit={frameUnit(frame)}
                data={frameAtr(frame)}
                labels={frameLabels(frame)}
                sub={frame === "1d" ? [
                  { k: "2d", data: atrRep?.daily?.sub?.["2d"] },
                  { k: "3d", data: atrRep?.daily?.sub?.["3d"] },
                ] : undefined} />
            </div>

            {/* Layer-IV sleeve context — the pyramid runs inside the
                30% trading sleeve; show its caps + margin buffer
                verdict. Reflects the equity/leverage inputs. */}
            {prev?.sleeve?.enabled && prev.sleeve.state && (
              <div className="glass-tile p-3">
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-[10px] uppercase text-dim">
                    Trading sleeve — {pct(prev.sleeve.config.sleeve_pct)} of
                    equity · {fmtNum(prev.sleeve.config.target_leverage, 0)}×
                  </span>
                  {prev.sleeve.state.buffer_capped ? (
                    <span className="rounded bg-neg/20 px-1.5 py-0.5 text-[9px] text-neg">
                      maint margin caps gross at {fmtNum(prev.sleeve.state.effective_gross_cap / prev.sleeve.state.sleeve_equity, 1)}×
                    </span>
                  ) : (
                    <span className="rounded bg-pos/20 px-1.5 py-0.5 text-[9px] text-pos">
                      margin buffer ok
                    </span>
                  )}
                </div>
                <div className="grid grid-cols-3 gap-x-3 sm:grid-cols-6">
                  {[
                    ["Sleeve equity", `$${fmtNum(prev.sleeve.state.sleeve_equity, 0)}`],
                    ["Gross cap", `$${fmtNum(prev.sleeve.state.effective_gross_cap, 0)}`],
                    ["Max / asset", `$${fmtNum(prev.sleeve.state.max_asset_notional, 0)}`],
                    ["Starter cap", `$${fmtNum(prev.sleeve.state.starter_notional, 0)}`],
                    ["Risk budget", `$${fmtNum(prev.sleeve.state.per_trade_risk_budget, 0)}`],
                    ["Free margin", `$${fmtNum(prev.sleeve.state.free_margin, 0)}`],
                  ].map(([l, v]) => (
                    <div key={l as string}>
                      <div className="text-[9px] uppercase text-faint">{l as string}</div>
                      <div className="num text-[12px] text-text">{v as string}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Trade Eligibility — the unified gate the create call
                consults: quality × valuation × technical × margin ×
                portfolio. Non-eligible requires an explicit override. */}
            {prev?.eligibility && (
              <div className="glass-tile p-3">
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-[10px] uppercase text-dim">Trade eligibility</span>
                  <StatusBadge tone={
                    prev.eligibility.verdict === "TRADE_ELIGIBLE" ? "pos"
                    : prev.eligibility.verdict === "BLOCKED" ? "warn"
                    : "neg"}>
                    {prev.eligibility.verdict}
                  </StatusBadge>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {(["quality", "valuation", "technical", "macro",
                     "margin", "portfolio"] as const).map((g) => {
                    const gate = prev.eligibility.gates?.[g];
                    if (!gate) return null;
                    return (
                      <span key={g}
                        className={`rounded px-1.5 py-0.5 text-[9px] ${
                          gate.pass
                            ? "bg-pos/15 text-pos"
                            : "bg-neg/15 text-neg"}`}>
                        {g}{gate.skipped ? "·n/a" : ""}
                      </span>
                    );
                  })}
                </div>
                {prev.eligibility.blocking?.length > 0 && (
                  <div className="mt-1 text-[10px] text-neg">
                    blocking: {prev.eligibility.blocking.join(", ")}
                  </div>
                )}
                {prev.eligibility.verdict !== "TRADE_ELIGIBLE" && (
                  <label className="mt-2 flex items-center gap-2 text-[10px] text-dim">
                    <input type="checkbox" checked={force}
                      onChange={(e) => setForce(e.target.checked)}
                      className="accent-[#f0b429]" />
                    Authorized override — create despite
                    {" "}{prev.eligibility.verdict} (audited)
                  </label>
                )}
              </div>
            )}

            {/* Trade Risk Sheet — the selected ATR frame's sheet:
                same workbook, this frame's ATR → its stop/target/size */}
            {prev ? (
              !sh || sh.status === "insufficient_data" ? (
                <div className="glass-tile p-3">
                  <div className="mb-1 text-[10px] uppercase text-dim">
                    Trade Risk Sheet — {frameTitle}
                  </div>
                  <div className="py-3 text-[11px] text-faint">
                    insufficient {frameTitle.toLowerCase()} history
                  </div>
                </div>
              ) : (
                <div className="glass-tile p-3">
                  <div className="mb-1 flex items-center justify-between">
                    <span className="text-[10px] uppercase text-dim">
                      Risk Sheet — {frameTitle}
                    </span>
                    <span className="num text-[9px] text-faint">
                      ATR {pct(sh.atr_pct)}
                    </span>
                  </div>
                  <SheetRow label="Stop (−1.5×ATR)"
                    value={`$${fmtNum(sh.stop, 2)}`} tone="text-neg" />
                  <SheetRow label="Target (+3×ATR)"
                    value={`$${fmtNum(sh.target, 2)}`} tone="text-pos" />
                  <SheetRow label="Risk / share"
                    value={`$${fmtNum(sh.risk_per_share, 2)}`} />
                  <SheetRow label="Shares"
                    value={`${sh.shares}  (${sh.binding})`} />
                  <SheetRow label="$ at risk"
                    value={`$${fmtNum(sh.dollar_risk, 0)}`} />
                  <SheetRow label="Notional"
                    value={`$${fmtNum(sh.notional, 0)}`} />
                  {sh.margin_required > 0 &&
                    <SheetRow label="Margin req."
                      value={`$${fmtNum(sh.margin_required, 0)}`} />}
                  <SheetRow label="R : R"
                    value={`${Math.round(sh.rr)} : 1`} />
                  <SheetRow label="Open risk"
                    value={pct(sh.open_risk_pct)} />
                  {(sh.legs ?? []).length > 0 && (
                    <div className="mt-1 border-t border-border/50 pt-1 text-[10px] text-faint">
                      {sh.legs.map((l: any) =>
                        `T${l.leg} $${fmtNum(l.fill, 0)}`).join(" · ")}
                    </div>
                  )}
                </div>
              )
            ) : !err && (
              <div className="flex items-center justify-center gap-2 py-4 text-[12px] text-faint">
                <span className="size-3 animate-spin rounded-full border border-faint/40 border-t-accent" />
                calculating…
              </div>
            )}
          </div>

          {/* ── right — price history beside the ATR card for
              side-by-side observation; fixed height so the table
              scrolls inside the card instead of stretching the modal ── */}
          <div className="h-[420px] min-h-0 self-start xl:h-[560px]">
            <PriceHistory sym={sym} />
          </div>
        </div>
      )}

      {/* footer actions */}
      {sym && atrRep && !atrRep.insufficient && (
        <div className="mt-3 flex flex-col gap-2 border-t border-border pt-3 sm:flex-row sm:items-center sm:justify-between">
          <span className="text-[10px] text-faint">
            Creates a Position-1 pyramid record — sized plan + stop/target
            levels. No order is staged or routed; execution stays behind
            approval.
          </span>
          <div className="flex gap-2">
            <button onClick={onClose}
              className="rounded-full border border-border px-3 py-1 text-[11px] text-dim hover:text-text">
              Cancel
            </button>
            <button onClick={create}
              disabled={!prev || busy || (
                prev?.eligibility &&
                prev.eligibility.verdict !== "TRADE_ELIGIBLE" &&
                !force)}
              className={`rounded-full px-4 py-1 text-[11px] font-semibold hover:brightness-110 disabled:opacity-40 ${
                force && prev?.eligibility?.verdict !== "TRADE_ELIGIBLE"
                  ? "bg-warn text-[#0b0f1a]"
                  : "bg-accent text-[#0b0f1a]"}`}>
              {busy ? "Creating…"
                : force && prev?.eligibility?.verdict !== "TRADE_ELIGIBLE"
                  ? "Override & create"
                  : "Create pyramid"}
            </button>
          </div>
        </div>
      )}
    </Dialog>
  );
}
