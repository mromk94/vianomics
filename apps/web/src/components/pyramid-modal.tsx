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

/** One timeframe table from the Excel "ATR Output" sheet — current
 * SMA14(TR)/close plus the workbook's horizon-average rows. */
function AtrTable({ title, data, labels }:
  { title: string; data: any; labels: Record<string, string> }) {
  const wins = Object.entries(data?.windows ?? {});
  return (
    <div className="glass-tile p-3">
      <div className="text-[10px] uppercase tracking-wide text-dim">{title}</div>
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
              <span className="text-faint">{w}{title.startsWith("Daily") ? "d" : title.startsWith("Weekly") ? "w" : "m"} </span>
              {pct(v as number, 2)}
              <span className="ml-1 text-[9px] text-faint">{labels[w]}</span>
            </div>
          ))}
        </div>
      )}
      <div className="mt-1 text-[9px] text-faint">{data?.bars ?? 0} bars</div>
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
  const [atrRep, setAtrRep] = useState<any | null>(null);
  const [prev, setPrev] = useState<any | null>(null);
  const [riskPct, setRiskPct] = useState("0.5");
  const [entry, setEntry] = useState("");
  const [equity, setEquity] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const debRef = useRef<ReturnType<typeof setTimeout>>(null);

  // pre-seed with the charted symbol
  useEffect(() => {
    if (open && initial && !loaded) selectSym(initial);
    if (!open) { setLoaded(false); setErr(null); }
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

  async function selectSym(s: string) {
    setSym(s.toUpperCase()); setQ(""); setHits([]);
    setErr(null); setPrev(null); setAtrRep(null);
    setLoaded(true);
    try {
      const [a, p] = await Promise.all([
        apiGet(`/api/v1/risk/atr/${s.toUpperCase()}`),
        apiPost<any>("/api/v1/risk/pyramid/preview", {
          symbol: s.toUpperCase(),
          risk_pct: parseFloat(riskPct) / 100 || 0.005,
        }),
      ]);
      setAtrRep(a); setPrev(p);
      setEntry(String(p.inputs.entry));
      setEquity(String(Math.round(p.inputs.equity)));
    } catch (e) {
      try {
        const a = await apiGet(`/api/v1/risk/atr/${s.toUpperCase()}`);
        setAtrRep(a);
        setErr("atr loaded — preview unavailable (insufficient bars or no portfolio equity)");
      } catch {
        setErr(e instanceof Error ? e.message : "no ATR data for symbol");
      }
    }
  }

  async function recalc() {
    if (!sym) return;
    setErr(null);
    try {
      const p = await apiPost<any>("/api/v1/risk/pyramid/preview", {
        symbol: sym,
        risk_pct: parseFloat(riskPct) / 100 || 0.005,
        entry: entry ? parseFloat(entry) : undefined,
        equity: equity ? parseFloat(equity) : undefined,
      });
      setPrev(p);
    } catch (e) {
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
    <Dialog open={open} onClose={onClose} wide
      title="Pyramid Calculator — ATR Output & Trade Risk Sheet">
      {/* ticker search */}
      <div className="relative">
        <SearchInput value={q} onChange={setQ}
          placeholder="Search ticker — AAPL, NVDA, XLK…" />
        {hits.length > 0 && (
          <div className="absolute z-10 mt-1 w-full rounded border border-border bg-surface-2 shadow-lg">
            {hits.map((h) => (
              <button key={h.id} onClick={() => selectSym(h.symbol)}
                className="flex w-full items-center justify-between px-3 py-1.5 text-left text-[12px] hover:bg-surface">
                <span><b className="num">{h.symbol}</b>
                  <span className="ml-2 text-dim">{h.name}</span></span>
                <span className="text-[10px] text-faint">{h.asset_class}</span>
              </button>
            ))}
          </div>
        )}
      </div>

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
          {/* ATR Output sheet — daily / weekly / monthly */}
          <div className="grid gap-2 sm:grid-cols-3">
            <AtrTable title="Daily ATR" data={atrRep.daily} labels={DAY_LABELS} />
            <AtrTable title="Weekly ATR" data={atrRep.weekly} labels={WK_LABELS} />
            <AtrTable title="Monthly ATR" data={atrRep.monthly} labels={MO_LABELS} />
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
                  onBlur={recalc}
                  onKeyDown={(e) => e.key === "Enter" && recalc()}
                  className="num mt-0.5 w-full rounded border border-border bg-surface-2 px-2 py-1 text-[12px] focus:border-accent focus:outline-none" />
                <span className="text-[9px] text-faint">{hint as string}</span>
              </label>
            ))}
            <div className="flex items-end">
              <button onClick={recalc}
                className="w-full rounded border border-border px-2 py-1 text-[11px] text-dim hover:text-text">
                Recalculate
              </button>
            </div>
          </div>

          {/* Trade Risk Sheet output */}
          {prev ? (
            <div className="grid gap-2 sm:grid-cols-2">
              <div className="glass-tile p-3">
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-[10px] uppercase text-dim">Trade Risk Sheet</span>
                  <StatusBadge tone={prev.vol_regime === "expanding" ? "warn" : "info"}>
                    {prev.vol_regime}
                  </StatusBadge>
                </div>
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
          <div className="flex items-center justify-between border-t border-border pt-3">
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
