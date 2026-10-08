"use client";

 import { useEffect, useMemo, useState } from "react";
import { Search, ShieldCheck, X } from "lucide-react";

import { apiGet, apiPost, ApiError, getToken } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Drawer } from "@/components/ui/drawer";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { FilterSelect } from "@/components/ui/filter-select";
import { PageHeader } from "@/components/ui/page-header";
import { Sym } from "@/components/symbol-drawer";
import { SearchInput } from "@/components/ui/search-input";
import { SectionCard } from "@/components/ui/section-card";
import { StatusBadge } from "@/components/ui/status-badge";

interface SearchRow {
  id: string;
  symbol: string;
  name: string;
  asset_class: string;
  listing_status: string;
  research_eligible: boolean;
  reasons: string[];
  market_cap: number | null;
}

interface Detail extends SearchRow {
  currency: string;
  avg_dollar_volume_30d: number | null;
  identifiers: { scheme: string; value: string; primary: boolean }[];
  eligibility: { known: boolean; research_eligible: boolean; reasons: string[] };
  universes: { name: string; tier: string; status: string; reason: string | null }[];
  approved_for_trading: boolean;
}

interface Hierarchy {
  global: number;
  core: number;
  eligible: number;
  approved: number;
  securities: number;
}

interface Watchlist {
  id: string;
  name: string;
  items: { symbol: string; name: string; note: string | null; reason_code: string | null }[];
}

const ASSET_CLASSES = ["equity", "future", "commodity", "crypto"];

export function UniversePage() {
  const [hier, setHier] = useState<Hierarchy | null>(null);
  const [rows, setRows] = useState<SearchRow[]>([]);
  const [lists, setLists] = useState<Watchlist[]>([]);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [q, setQ] = useState("");
  const [cls, setCls] = useState("");
  const [tier, setTier] = useState("");
  const [eligOnly, setEligOnly] = useState(false);
  const [showInactive, setShowInactive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setError(null);
    const qs = new URLSearchParams();
    qs.set("limit", "2000");
    if (q) qs.set("q", q);
    if (cls) qs.set("asset_class", cls);
    if (tier) qs.set("universe", tier);
    if (eligOnly) qs.set("eligible_only", "true");
    if (showInactive) qs.set("include_inactive", "true");
    Promise.all([
      apiGet<Hierarchy>("/api/v1/universe"),
      apiGet<SearchRow[]>(`/api/v1/universe/instruments?${qs}`),
      apiGet<Watchlist[]>("/api/v1/universe/watchlists"),
    ])
      .then(([h, r, w]) => {
        setHier(h);
        setRows(r);
        setLists(w);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [cls, tier, eligOnly, showInactive]);
  useEffect(() => {
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const openDetail = (symbol: string) =>
    apiGet<Detail>(`/api/v1/universe/instruments/${symbol}`)
      .then(setDetail)
      .catch((e) => setNotice(e.message));

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      setNotice(null);
      load();
      if (detail) openDetail(detail.symbol);
    } catch (e) {
      setNotice(
        e instanceof ApiError && e.status === 401
          ? "Sign in (Settings) with an authorized account to change universe membership."
          : (e as Error).message,
      );
    }
  };

  const membership = (status: "active" | "excluded") =>
    detail &&
    act(() =>
      apiPost(`/api/v1/universe/instruments/${detail.symbol}/membership`, {
        universe: "approved",
        status,
        reason:
          status === "excluded"
            ? window.prompt("Exclusion reason (required)") ?? ""
            : null,
      }),
    );

  const columns: Column<SearchRow>[] = useMemo(
    () => [
      {
        key: "symbol",
        header: "Ticker",
        render: (r) => (
          <button
            onClick={() => openDetail(r.symbol)}
            className="font-semibold text-accent hover:underline" data-sym
          >
            {r.symbol}
          </button>
        ),
      },
      { key: "name", header: "Name", render: (r) => <span className="text-dim">{r.name}</span> },
      { key: "class", header: "Class", render: (r) => <StatusBadge tone="info">{r.asset_class}</StatusBadge> },
      {
        key: "status",
        header: "Listing",
        render: (r) => (
          <StatusBadge tone={r.listing_status === "active" ? "pos" : "neg"} dot>
            {r.listing_status}
          </StatusBadge>
        ),
      },
      {
        key: "mcap",
        header: "Mkt Cap",
        align: "right",
        render: (r) =>
          r.market_cap ? `$${fmtNum(r.market_cap / 1e9, 1)}B` : "—",
      },
      {
        key: "elig",
        header: "Eligibility",
        render: (r) =>
          r.research_eligible ? (
            <StatusBadge tone="pos" dot>eligible</StatusBadge>
          ) : (
            <span title={r.reasons.join("; ")}>
              <StatusBadge tone="warn">blocked</StatusBadge>
            </span>
          ),
      },
    ],
    [],
  );

  return (
    <div className="space-y-4">
      <PageHeader
        title="Investment Universe"
        subtitle="Security master — Global → Eligible → Approved → Securities"
        info="The set of stocks the system may evaluate — mandate filters (sector, market cap, exclusions) apply here first."
      />
      {notice && (
        <div className="glass border-warn/40 p-3 text-[13px] text-warn">{notice}</div>
      )}
      {error && (
        <ErrorState title="Cannot reach the VAIIP API" detail={error} onRetry={load} />
      )}

      {/* hierarchy strip */}
      {hier && (
        <div className="rise grid grid-cols-2 gap-3 sm:grid-cols-5">
          {[
            ["Global Market", hier.global],
            ["Core Market", hier.core],
            ["Eligible Markets", hier.eligible],
            ["Approved Universe", hier.approved],
            ["Securities", hier.securities],
          ].map(([label, n], i) => (
            <div key={label as string} className="glass p-4">
              <div className="text-[11px] tracking-wider text-dim uppercase">{label}</div>
              <div className="num mt-1 text-2xl font-semibold">{n}</div>
            </div>
          ))}
        </div>
      )}

      <SectionCard title="Security Master" className="rise rise-1">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <SearchInput
            value={q}
            onChange={setQ}
            placeholder="Search ticker or name…"
            className="w-64"
          />
          <FilterSelect
            value={cls}
            onChange={setCls}
            options={[{ value: "", label: "All classes" }, ...ASSET_CLASSES.map((c) => ({ value: c, label: c }))]}
          />
          <FilterSelect
            value={tier}
            onChange={setTier}
            options={[
              { value: "", label: "All tiers" },
              { value: "global", label: "Global" },
              { value: "core", label: "Core market" },
              { value: "eligible", label: "Eligible" },
              { value: "approved", label: "Approved" },
            ]}
          />
          <label className="flex items-center gap-2 text-[13px] text-dim">
            <input type="checkbox" checked={eligOnly} onChange={(e) => setEligOnly(e.target.checked)} className="accent-[#f0b90b]" />
            Eligible only
          </label>
          <label className="flex items-center gap-2 text-[13px] text-dim">
            <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} className="accent-[#f0b90b]" />
            Include delisted
          </label>
          {rows.length > 0 && hier && (
            <span className="num ml-auto text-[11px] text-faint">
              {rows.length} listed · {hier.securities} in master
              {rows.length >= 2000 && " — search to narrow"}
            </span>
          )}
        </div>
        <DataTable
          columns={columns}
          rows={rows}
          loading={loading}
          rowKey={(r) => r.id}
          empty="No securities match"
        />
      </SectionCard>

      {/* Watchlists */}
      <SectionCard title="Watchlists" className="rise rise-2"
        action={getToken() ? undefined : "sign in to manage"}>
        {lists.length === 0 ? (
          <EmptyState title="No watchlists" hint="Create one to track tickers below the Green Zone threshold." />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {lists.map((w) => (
              <div key={w.id} className="glass-tile p-3">
                <div className="mb-2 flex items-center justify-between text-[13px] font-semibold">
                  {w.name}
                  <span className="text-[11px] text-faint">{w.items.length} items</span>
                </div>
                {w.items.length === 0 ? (
                  <div className="text-[12px] text-faint">Empty</div>
                ) : (
                  <ul className="space-y-1 text-[13px]">
                    {w.items.map((it) => (
                      <li key={it.symbol} className="flex justify-between">
                        <span className="font-medium text-accent">{it.symbol}</span>
                        <span className="text-faint">{it.reason_code ?? it.note ?? ""}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        )}
      </SectionCard>

      {/* Detail drawer */}
      <Drawer open={!!detail} onClose={() => setDetail(null)} title={detail ? `${detail.symbol} — ${detail.name}` : ""}>
        {detail && (
          <div className="space-y-4 text-[13px]">
            <div className="flex flex-wrap gap-2">
              <StatusBadge tone={detail.listing_status === "active" ? "pos" : "neg"} dot>
                {detail.listing_status}
              </StatusBadge>
              <StatusBadge tone="info">{detail.asset_class}</StatusBadge>
              {detail.approved_for_trading && <StatusBadge tone="pos">approved</StatusBadge>}
            </div>

            <div className="glass-tile p-3">
              <div className="mb-1.5 text-[11px] tracking-wider text-dim uppercase">Eligibility</div>
              {detail.eligibility.research_eligible ? (
                <div className="flex items-center gap-1.5 text-pos">
                  <ShieldCheck className="size-4" /> Eligible for research
                </div>
              ) : (
                <ul className="space-y-1">
                  {detail.eligibility.reasons.map((r) => (
                    <li key={r} className="flex items-start gap-1.5 text-warn">
                      <X className="mt-0.5 size-3 shrink-0" /> {r}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="glass-tile p-3">
              <div className="mb-1.5 text-[11px] tracking-wider text-dim uppercase">Identifiers</div>
              {detail.identifiers.map((i) => (
                <div key={i.scheme} className="flex justify-between py-0.5">
                  <span className="uppercase text-dim">{i.scheme}</span>
                  <span className="num">{i.value}</span>
                </div>
              ))}
              {detail.identifiers.length === 0 && <div className="text-faint">None registered</div>}
            </div>

            <div className="glass-tile p-3">
              <div className="mb-1.5 text-[11px] tracking-wider text-dim uppercase">Universe Memberships</div>
              {detail.universes.map((u) => (
                <div key={u.name} className="flex items-center justify-between py-0.5">
                  <span className="capitalize">{u.name}</span>
                  <span className="flex items-center gap-2">
                    <StatusBadge tone={u.status === "active" ? "pos" : u.status === "excluded" ? "neg" : "warn"}>
                      {u.status}
                    </StatusBadge>
                  </span>
                </div>
              ))}
              {detail.universes.some((u) => u.reason) && (
                <div className="mt-2 border-t border-border pt-2 text-[12px] text-faint">
                  {detail.universes.filter((u) => u.reason).map((u) => (
                    <div key={u.name}>{u.name}: {u.reason}</div>
                  ))}
                </div>
              )}
            </div>

            {getToken() && (
              <div className="flex gap-2">
                <button
                  onClick={() => membership("active")}
                  className="rounded-full bg-pos/15 px-4 py-1.5 text-[13px] font-medium text-pos transition-colors hover:bg-pos/25"
                >
                  Approve for trading
                </button>
                <button
                  onClick={() => membership("excluded")}
                  className="rounded-full bg-neg/15 px-4 py-1.5 text-[13px] font-medium text-neg transition-colors hover:bg-neg/25"
                >
                  Exclude…
                </button>
              </div>
            )}
          </div>
        )}
      </Drawer>
    </div>
  );
}
