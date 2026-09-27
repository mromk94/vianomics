"use client";

import { useEffect, useState } from "react";

import { apiGet, apiPost, ApiError, getToken, setToken } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { SkeletonRows } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";

interface Mandate {
  id: string;
  version: number;
  is_active: boolean;
  effective_from: string | null;
  change_note: string | null;
  created_at: string | null;
  investment_split_pct: number;
  trading_split_pct: number;
  inv_min_stocks: number;
  inv_max_stocks: number;
  inv_horizon_years_min: number;
  inv_horizon_years_max: number;
  trading_max_stocks: number;
  trading_horizon: string;
  target_return_min: number;
  target_return_max: number;
  max_drawdown_pct: number;
  min_sharpe: number;
  min_win_rate_pct: number;
  min_risk_reward: number;
  green_zone_pass_score: number;
  margin_of_safety_min_pct: number;
  extra: Record<string, unknown>;
}

interface Me {
  email: string;
  permissions: string[];
}

const FIELDS: { key: keyof Mandate; label: string; suffix?: string }[] = [
  { key: "investment_split_pct", label: "Investment split", suffix: "%" },
  { key: "trading_split_pct", label: "Trading split", suffix: "%" },
  { key: "inv_min_stocks", label: "Inv. min stocks" },
  { key: "inv_max_stocks", label: "Inv. max stocks" },
  { key: "inv_horizon_years_min", label: "Horizon min", suffix: "yrs" },
  { key: "inv_horizon_years_max", label: "Horizon max", suffix: "yrs" },
  { key: "trading_max_stocks", label: "Trading max stocks" },
  { key: "target_return_min", label: "Return target min", suffix: "%" },
  { key: "target_return_max", label: "Return target max", suffix: "%" },
  { key: "max_drawdown_pct", label: "Max drawdown", suffix: "%" },
  { key: "min_sharpe", label: "Min Sharpe" },
  { key: "min_win_rate_pct", label: "Min win rate", suffix: "%" },
  { key: "min_risk_reward", label: "Min reward:risk" },
  { key: "green_zone_pass_score", label: "Green Zone pass", suffix: "/20" },
  { key: "margin_of_safety_min_pct", label: "Min margin of safety", suffix: "%" },
];

export function MandatePage() {
  const [me, setMe] = useState<Me | null>(null);
  const [active, setActive] = useState<Mandate | null>(null);
  const [versions, setVersions] = useState<Mandate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const [login, setLogin] = useState({ email: "", password: "" });

  const canWrite = me?.permissions.some(
    (p) => p === "admin:*" || p === "mandate:write" || p === "mandate:*",
  );

  const load = async () => {
    setError(null);
    try {
      const [a, v] = await Promise.all([
        apiGet<Mandate>("/api/v1/mandate/active"),
        getToken() ? apiGet<Mandate[]>("/api/v1/mandate/versions") : Promise.resolve([]),
      ]);
      setActive(a);
      setVersions(v);
      if (getToken() && !me) {
        setMe(await apiGet<Me>("/api/v1/auth/me").catch(() => null));
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, []);

  const doLogin = async () => {
    try {
      const r = await apiPost<{ token: string }>("/api/v1/auth/login", login);
      setToken(r.token);
      setMe(await apiGet<Me>("/api/v1/auth/me"));
      setNotice(null);
      load();
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : "login failed");
    }
  };

  const startEdit = () => {
    if (!active) return;
    const f: Record<string, string> = {};
    for (const { key } of FIELDS) f[key] = String(active[key]);
    f["trading_horizon"] = active.trading_horizon;
    setForm(f);
    setEditing(true);
  };

  const save = async () => {
    try {
      const body: Record<string, unknown> = { change_note: note };
      for (const { key } of FIELDS) {
        const v = form[key];
        body[key] = Number.isNaN(Number(v)) ? v : Number(v);
      }
      body["trading_horizon"] = form["trading_horizon"] || "days_weeks";
      await apiPost("/api/v1/mandate/versions", body);
      setEditing(false);
      setNote("");
      setNotice("New mandate version created.");
      load();
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : (e as Error).message);
    }
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Settings & Investment Mandate"
        subtitle="Constitutional constraints — versioned, audited, effective-dated"
      />
      {notice && <div className="glass border-warn/40 p-3 text-[13px] text-warn">{notice}</div>}
      {error && <ErrorState title="Cannot reach the VAIIP API" detail={error} onRetry={load} />}

      {/* auth */}
      {!me && (
        <SectionCard title="Sign in" className="rise">
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-[12px] text-dim">
              Email
              <input
                type="email" value={login.email} autoComplete="username"
                onChange={(e) => setLogin({ ...login, email: e.target.value })}
                className="mt-1 block w-56 rounded-md border border-border bg-surface-2 px-3 py-1.5 text-text"
              />
            </label>
            <label className="text-[12px] text-dim">
              Password
              <input
                type="password" value={login.password} autoComplete="current-password"
                onChange={(e) => setLogin({ ...login, password: e.target.value })}
                onKeyDown={(e) => e.key === "Enter" && doLogin()}
                className="mt-1 block w-48 rounded-md border border-border bg-surface-2 px-3 py-1.5 text-text"
              />
            </label>
            <button
              onClick={doLogin}
              className="rounded-full bg-accent px-4 py-1.5 text-[13px] font-semibold text-[#0b0f1a] transition hover:brightness-110"
            >
              Sign in
            </button>
          </div>
        </SectionCard>
      )}
      {me && (
        <div className="rise text-[12px] text-dim">
          Signed in as <strong className="text-text">{me.email}</strong> ·{" "}
          {me.permissions.join(", ")}{" "}
          <button
            className="text-accent hover:underline"
            onClick={() => { setToken(null); setMe(null); setVersions([]); }}
          >
            sign out
          </button>
        </div>
      )}

      {/* active mandate */}
      <SectionCard
        title={active ? `Investment Mandate — v${active.version}` : "Investment Mandate"}
        className="rise rise-1"
        action={active ? `effective ${fmtTime(active.effective_from ?? active.created_at)}` : undefined}
      >
        {loading ? (
          <SkeletonRows />
        ) : !active ? (
          <EmptyState title="No mandate configured" hint="Create version 1 to establish constitutional limits." />
        ) : (
          <>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5">
              {FIELDS.map(({ key, label, suffix }) => (
                <div key={key} className="glass-tile px-3 py-2">
                  <div className="text-[10px] tracking-wider text-dim uppercase">{label}</div>
                  <div className="num mt-0.5 text-base font-semibold">
                    {String(active[key])}{suffix ?? ""}
                  </div>
                </div>
              ))}
              <div className="glass-tile px-3 py-2">
                <div className="text-[10px] tracking-wider text-dim uppercase">Trading horizon</div>
                <div className="mt-0.5 text-base font-semibold">{active.trading_horizon}</div>
              </div>
            </div>
            <div className="mt-3 flex items-center justify-between text-[12px] text-faint">
              <span>
                Objectives are targets, not guarantees.
                {active.change_note && <> Note: {active.change_note}</>}
              </span>
              {canWrite && !editing && (
                <button onClick={startEdit}
                  className="rounded-full border border-accent/50 px-4 py-1 text-[12px] font-medium text-accent transition hover:bg-accent-soft">
                  New version
                </button>
              )}
            </div>
          </>
        )}
      </SectionCard>

      {/* version editor */}
      {editing && canWrite && (
        <SectionCard title={`New mandate version — v${(active?.version ?? 0) + 1}`} className="rise">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
            {FIELDS.map(({ key, label }) => (
              <label key={key} className="text-[11px] tracking-wide text-dim">
                {label}
                <input
                  value={form[key] ?? ""}
                  onChange={(e) => setForm({ ...form, [key]: e.target.value })}
                  className="num mt-1 block w-full rounded-md border border-border bg-surface-2 px-2.5 py-1.5 text-text"
                />
              </label>
            ))}
            <label className="text-[11px] tracking-wide text-dim">
              Trading horizon
              <input
                value={form["trading_horizon"] ?? "days_weeks"}
                onChange={(e) => setForm({ ...form, trading_horizon: e.target.value })}
                className="mt-1 block w-full rounded-md border border-border bg-surface-2 px-2.5 py-1.5 text-text"
              />
            </label>
          </div>
          <label className="mt-3 block text-[11px] tracking-wide text-dim">
            Change note (required — becomes part of the audit trail)
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="e.g. Tighten drawdown after vol regime shift"
              className="mt-1 block w-full rounded-md border border-border bg-surface-2 px-3 py-1.5 text-text"
            />
          </label>
          <div className="mt-3 flex gap-2">
            <button
              onClick={save}
              disabled={note.trim().length < 5}
              className="rounded-full bg-accent px-4 py-1.5 text-[13px] font-semibold text-[#0b0f1a] transition enabled:hover:brightness-110 disabled:opacity-40"
            >
              Save new version
            </button>
            <button onClick={() => setEditing(false)} className="rounded-full border border-border px-4 py-1.5 text-[13px] text-dim">
              Cancel
            </button>
          </div>
        </SectionCard>
      )}

      {/* version history */}
      <SectionCard title="Version history" className="rise rise-2">
        {versions.length === 0 ? (
          <EmptyState
            title={me ? "No history" : "Sign in to view history"}
            hint="Every mandate change creates an immutable version — past decisions reference the version in force at decision time."
          />
        ) : (
          <ul className="space-y-1.5">
            {versions.map((v) => (
              <li key={v.id} className="glass-tile flex items-center justify-between px-3.5 py-2 text-[13px]">
                <span className="flex items-center gap-3">
                  <span className="font-semibold">v{v.version}</span>
                  {v.is_active && <StatusBadge tone="pos">active</StatusBadge>}
                  <span className="text-dim">{v.change_note}</span>
                </span>
                <span className="num text-[12px] text-faint">
                  eff. {fmtTime(v.effective_from ?? v.created_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </SectionCard>
    </div>
  );
}
