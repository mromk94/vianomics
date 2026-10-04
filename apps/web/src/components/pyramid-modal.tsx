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

function pct(v: number | null | undefined, d = 2) {
  return v == null ? "—" : `${(v * 100).toFixed(d)}%`;
}

/** One timeframe table from the Excel "ATR Output" sheet — editable
 * SMA period (3d, 8d, 2w, 5m…) + horizon-average rows. */
function AtrTable({ title, unit, data, labels, period, onPeriod }:
  { title: string; unit: string; data: any;
    labels: Record<string, string>;
    period: number; onPeriod: (v: number) => void }) {
  const wins = Object.entries(data?.windows ?? {});
  return (
    <div className="glass-tile p-3">
      <div className="flex items-center justify-between">
        <div className="text-[10px] uppercase tracking-wide text-dim">{title}</div>
        <label className="flex items-center gap-1 text-[9px] text-faint">
          SMA
          <input type="number" min={1} max={200} value={period}
            onChange={(e) => onPeriod(parseInt(e.target.value) || 14)}
            className="num w-11 rounded border border-border bg-surface-2 px-1 py-0.5 text-[10px] focus:border-accent focus:outline-none" />
          {unit}
        </label>
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
      <div className="mt-1 text-[9px] text-faint">{data?.bars ?? 0} bars · SMA{data?.period ?? period}(TR)</div>
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
  const [riskPct, setRiskPct] = useState("0.5");
  const [entry, setEntry] = useState("");
  const [equity, setEquity] = useState("");
  const [dP, setDP] = useState(14);
  const [wP, setWP] = useState(14);
  const [mP, setMP] = useState(14);
  const [tvOpen, setTvOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const debRef = useRef<ReturnType<typeof setTimeout>>(null);
  const atrRef = useRef<ReturnType<typeof setTimeout>>(null);

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

  // ATR period changes → refetch the sheet (debounced)
  useEffect(() => {
    if (!sym) return;
    if (atrRef.current) clearTimeout(atrRef.current);
    atrRef.current = setTimeout(async () => {
      try {
        const a = await apiGet<any>(
          `/api/v1/risk/atr/${sym}?d=${dP}&w=${wP}&m=${mP}`);
        setAtrRep(a);
        // pyramid levels follow the daily ATR — refresh the sheet
        if (a?.daily?.atr_abs) recalc(a.daily.atr_abs);
      } catch { /* keep last good sheet */ }
    }, 350);
    return () => { if (atrRef.current) clearTimeout(atrRef.current); };
  }, [dP, wP, mP]); // eslint-disable-line react-hooks/exhaustive-deps

  async function selectSym(s: string, hitName?: string) {
    const up = s.toUpperCase();
    setSym(up); setName(hitName ?? null);
    setQ(""); setHits([]);
    setErr(null); setPrev(null); setAtrRep(null);
    setLoaded(true);
    try {
      const a = await apiGet<any>(
        `/api/v1/risk/atr/${up}?d=${dP}&w=${wP}&m=${mP}`);
      setAtrRep(a);
      if (a?.name) setName(a.name);
      if (a?.insufficient) {
        setErr(a.note ?? "insufficient bars");
        return;
      }
      await recalc(a?.daily?.atr_abs);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "no ATR data for symbol");
    }
  }

  async function recalc(atrAbs?: number) {
    if (!sym) return;
    try {
      const body: any = {
        symbol: sym,
        risk_pct: parseFloat(riskPct) / 100 || 0.005,
      };
      if (entry) body.entry = parseFloat(entry);
      if (equity) body.equity = parseFloat(equity);
      if (atrAbs) body.atr_override = atrAbs;
      const p = await apiPost<any>("/api/v1/risk/pyramid/preview", body);
      setPrev(p);
      if (!entry) setEntry(String(p.inputs.entry));
      if (!equity) setEquity(String(Math.round(p.inputs.equity)));
    } catch (e) {
      setPrev(null);
      setErr(e instanceof Error ? e.message : "preview failed");
    }
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
      });
      onCreated();
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "create failed");
    } finally { setBusy(false); }
  }

  return (
    <Dialog open={open} onClose={onClose} size="xl"
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
            hint={atrRep.note ?? "needs ≥15 daily bars for SMA14(TR)"} />
        </div>
      )}

      {sym && atrRep && !atrRep.insufficient && (
        <div className="mt-3 space-y-3">
          {/* ATR Output sheet — editable periods */}
          <div className="grid gap-2 sm:grid-cols-3">
            <AtrTable title="Daily ATR" unit="d" data={atrRep.daily}
              labels={DAY_LABELS} period={dP} onPeriod={setDP} />
            <AtrTable title="Weekly ATR" unit="w" data={atrRep.weekly}
              labels={WK_LABELS} period={wP} onPeriod={setWP} />
            <AtrTable title="Monthly ATR" unit="m" data={atrRep.monthly}
              labels={MO_LABELS} period={mP} onPeriod={setMP} />
          </div>

          {/* inputs — Trade Risk Sheet parameters */}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[
              ["Entry", entry, setEntry, `$${fmtNum(atrRep.close, 2)} last`],
              ["Risk %", riskPct, setRiskPct, "of equity"],
              ["Equity $", equity, setEquity, "live NAV"],
            ].map(([l, v, set, hint]) => (
              <label key={l as string} className="block">
                <span className="text-[10px] uppercase text-dim">{l as string}</span>
                <input value={v as string}
                  onChange={(e) => (set as any)(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && recalc()}
                  className="num mt-0.5 w-full rounded border border-border bg-surface-2 px-2 py-1 text-[12px] focus:border-accent focus:outline-none" />
                <span className="text-[9px] text-faint">{hint as string}</span>
              </label>
            ))}
            <div className="flex items-end">
              <button onClick={() => recalc(atrRep?.daily?.atr_abs)}
                className="w-full rounded bg-accent px-2 py-1.5 text-[12px] font-semibold text-[#0b0f1a] hover:brightness-110">
                Recalculate
              </button>
            </div>
          </div>

          {/* Layer-IV sleeve context — the pyramid runs inside the 30%
              trading sleeve; show its caps + margin buffer verdict */}
          {prev?.sleeve?.enabled && prev.sleeve.state && (
            <div className="glass-tile p-3">
              <div className="mb-1 flex items-center justify-between">
                <span className="text-[10px] uppercase text-dim">
                  Trading sleeve — {pct(prev.sleeve.config.sleeve_pct)} of equity
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

          {/* Trade Risk Sheet output */}
          {prev ? (
            <div className="grid gap-2 sm:grid-cols-2">
              <div className="glass-tile p-3">
                <div className="mb-1 text-[10px] uppercase text-dim">Trade Risk Sheet</div>
                <SheetRow label="Stop (−1.5×ATR)" value={`$${fmtNum(prev.sheet.stop, 2)}`} tone="text-neg" />
                <SheetRow label="Target (+3×ATR)" value={`$${fmtNum(prev.sheet.target, 2)}`} tone="text-pos" />
                <SheetRow label="Risk / share" value={`$${fmtNum(prev.sheet.risk_per_share, 2)}`} />
                <SheetRow label="$ Risk budget" value={`$${fmtNum(prev.sheet.dollar_risk, 0)}`} />
                <SheetRow label="R : R" value={`${fmtNum(prev.sheet.rr, 1)} : 1`} />
                <SheetRow label="Open risk" value={pct(prev.sheet.open_risk_pct)} />
              </div>
              <div className="glass-tile p-3">
                <div className="mb-1 text-[10px] uppercase text-dim">Position size</div>
                <SheetRow label="Shares"
                  value={`${prev.sheet.shares}  (${prev.sheet.binding})`} />
                <SheetRow label="Raw shares" value={String(prev.sheet.shares_raw)} />
                <SheetRow label="Notional" value={`$${fmtNum(prev.sheet.notional, 0)}`} />
                {prev.sheet.margin_required > 0 &&
                  <SheetRow label="Margin req." value={`$${fmtNum(prev.sheet.margin_required, 0)}`} />}
                <div className="mt-2 border-t border-border/50 pt-1 text-[10px] text-faint">
                  legs: {prev.legs.map((l: any) =>
                    `T${l.leg} $${fmtNum(l.fill, 0)}`).join(" · ")}
                </div>
              </div>
            </div>
          ) : !err && <div className="py-4 text-center text-[12px] text-faint">calculating…</div>}

          {/* footer actions */}
          <div className="flex flex-col gap-2 border-t border-border pt-3 sm:flex-row sm:items-center sm:justify-between">
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
              <button onClick={create} disabled={!prev || busy}
                className="rounded-full bg-accent px-4 py-1 text-[11px] font-semibold text-[#0b0f1a] hover:brightness-110 disabled:opacity-40">
                {busy ? "Creating…" : "Create pyramid"}
              </button>
            </div>
          </div>
        </div>
      )}
    </Dialog>
  );
}
