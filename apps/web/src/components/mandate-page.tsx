"use client";

 import { useEffect, useState } from "react";

import { apiGet, apiPost, apiDelete, ApiError, getToken, setToken } from "@/lib/api";
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

interface EnvKey {
  label: string; var: string; set: boolean; where: string;
  unlocks: string;
}
interface EnvStatus {
  env: EnvKey[];
  models: { area: string; current: string; notes: string }[];
  execution_enabled: boolean;
  execution_broker: string;
  demo_fixtures: boolean;
}

/** API keys, data sources, models — shows what IS configured without
 * ever exposing values. Keys live in the server's .env — see /docs. */
function EnvSettings() {
  const [cfg, setCfg] = useState<EnvStatus | null>(null);
  useEffect(() => {
    apiGet<EnvStatus>("/api/v1/settings/env").then(setCfg).catch(() => {});
  }, []);
  if (!cfg) return null;
  return (
    <>
      <SectionCard title="API keys & data sources" className="rise"
        action={
          <a href="/docs#keys" className="text-accent text-[11px] hover:underline">
            setup guide →
          </a>
        }>
        <p className="mb-3 text-[11px] text-dim">
          Keys live in the server&apos;s <code className="text-accent">apps/api/.env</code> file — never
          typed into the browser, never shown back. Add a line like{" "}
          <code className="text-accent">TIINGO_API_KEY=your-key</code>, restart
          the API, and this list flips to ✓.
        </p>
        <ul className="space-y-1.5">
          {cfg.env.map((k) => (
            <KeyRow key={k.var} k={k} onSaved={() => {
              apiGet<EnvStatus>("/api/v1/settings/env").then(setCfg);
            }} />
          ))}
        </ul>
      </SectionCard>

      <SectionCard title="Execution & demo state" className="rise">
        <ul className="space-y-1.5">
          {cfg.models.map((m) => (
            <li key={m.area} className="glass-tile px-3 py-2">
              <div className="flex items-center gap-2 text-[12px]">
                <span className="font-medium">{m.area}</span>
                <StatusBadge tone="info">{m.current}</StatusBadge>
              </div>
              <div className="mt-0.5 text-[11px] text-dim">{m.notes}</div>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-[11px] text-dim">
          Execution: broker <b className="text-text">{cfg.execution_broker}</b> ·
          live trading <b className={cfg.execution_enabled ? "text-neg" : "text-pos"}>
            {cfg.execution_enabled ? "ENABLED" : "disabled"}</b> ·
          demo fixtures {cfg.demo_fixtures ? "on" : "off"}.
          Turning on live trading requires IBKR configured + human approval —
          see <a href="/docs#execution" className="text-accent hover:underline">docs → execution</a>.
        </p>
      </SectionCard>
    </>
  );
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
      if (e instanceof ApiError && e.status === 401) {
        setError(null);           // not an API outage — just signed out
      } else {
        setError((e as Error).message);
      }
    } finally {
      setLoading(false);
    }
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, []);

  const [signingIn, setSigningIn] = useState(false);
  const doLogin = async () => {
    setSigningIn(true);
    setNotice(null);
    try {
      const r = await apiPost<{ token: string }>("/api/v1/auth/login", login);
      setToken(r.token);
      setMe(await apiGet<Me>("/api/v1/auth/me"));
      load();
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : "login failed");
    } finally {
      setSigningIn(false);
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
        info="Configure the investment mandate, your account, API keys and models. Policy shapes analysis — it never auto-trades."
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
              disabled={signingIn}
              className="rounded-full bg-accent px-4 py-1.5 text-[13px] font-semibold text-[#0b0f1a] transition hover:brightness-110 disabled:opacity-50"
            >
              {signingIn ? "Signing in…" : "Sign in"}
            </button>
            {signingIn && (
              <span className="self-center text-[11px] text-faint">verifying…</span>
            )}
          </div>
        </SectionCard>
      )}
      {me && (
        <div className="rise text-[12px] text-dim">
          Signed in as <strong className="text-text">{me.email}</strong> ·{" "}
          {me.permissions.join(", ")}{" "}
          <button
            className="text-accent hover:underline"
            onClick={() => { setToken(null); setMe(null); setVersions([]); window.location.href = "/login"; }}
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

      <EnvSettings />
      <ModelManager me={me} />
      <Notifications me={me} />
      <DemoToggle me={me} />
      <AccountSecurity me={me} />
    </div>
  );
}

/* ── AI model manager — pick provider, paste key, choose model ── */

interface ModelCfg {
  id: string; provider: string; label: string; model: string;
  key_set: boolean; key_masked: string | null; base_url: string | null;
  enabled: boolean; is_default: boolean;
}
interface ModelCatalog {
  providers: { id: string; label: string; models: string[];
    where: string; needs_base_url?: boolean }[];
  configs: ModelCfg[]; note: string;
}

function ModelManager({ me }: { me: Me | null }) {
  const [cat, setCat] = useState<ModelCatalog | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [form, setForm] = useState({ provider: "anthropic", model: "",
    api_key: "", base_url: "", label: "", is_default: false });

  const load = () => apiGet<ModelCatalog>("/api/v1/settings/models")
    .then(setCat).catch(() => {});
  useEffect(() => { load(); }, []);

  const prov = cat?.providers.find((p) => p.id === form.provider);
  const save = async () => {
    setMsg(null);
    try {
      await apiPost("/api/v1/settings/models", {
        provider: form.provider,
        model: form.model || prov?.models[0] || "",
        api_key: form.api_key || null,
        base_url: form.base_url || null,
        label: form.label, is_default: form.is_default,
      });
      setForm({ ...form, api_key: "", label: "" });
      setMsg("Saved.");
      load();
    } catch (e) {
      setMsg(e instanceof ApiError && e.status === 403
        ? "Admin permission required."
        : e instanceof ApiError && e.status === 401
        ? "Sign in first." : (e as Error).message);
    }
  };

  return (
    <SectionCard title="AI models" className="rise"
      action={<a href="/docs#keys" className="text-accent text-[11px] hover:underline">where to get keys →</a>}>
      <p className="mb-3 text-[11px] text-dim">
        Today all analysis is deterministic math — no AI calls are made.
        When you add a provider here, agents can call it for narrative
        work; <b>they still can&apos;t trade</b>. Keys are stored on the
        server and shown masked.
      </p>

      {/* provider picker */}
      <div className="mb-3 flex flex-wrap gap-1.5">
        {cat?.providers.map((p) => (
          <button key={p.id}
            onClick={() => setForm({ ...form, provider: p.id, model: "" })}
            className={`rounded-full border px-3 py-1 text-[11px] transition ${
              form.provider === p.id
                ? "border-accent bg-accent/10 font-semibold text-accent"
                : "border-border text-dim hover:text-text"}`}>
            {p.label}
          </button>
        ))}
      </div>
      {prov && (
        <p className="mb-2 text-[11px] text-faint">Get a key: {prov.where}</p>
      )}
      <div className="grid grid-cols-2 gap-2">
        <label className="text-[11px] text-dim">Model
          {prov && prov.models.length > 0 ? (
            <select value={form.model}
              onChange={(e) => setForm({ ...form, model: e.target.value })}
              className="mt-1 block w-full rounded-md border border-border bg-surface-2 px-2 py-1.5 text-[12px] text-text">
              <option value="">— choose —</option>
              {prov.models.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          ) : (
            <input value={form.model} placeholder="model name"
              onChange={(e) => setForm({ ...form, model: e.target.value })}
              className="mt-1 block w-full rounded-md border border-border bg-surface-2 px-2 py-1.5 text-[12px] text-text" />
          )}
        </label>
        <label className="text-[11px] text-dim">API key
          <input type="password" value={form.api_key}
            placeholder={prov?.id === "ollama" ? "not needed — local" : "sk-…"}
            autoComplete="new-password"
            onChange={(e) => setForm({ ...form, api_key: e.target.value })}
            className="mt-1 block w-full rounded-md border border-border bg-surface-2 px-2 py-1.5 text-[12px] text-text" />
        </label>
        {prov?.needs_base_url && (
          <label className="col-span-12 md:col-span-2 text-[11px] text-dim">Base URL
            <input value={form.base_url} placeholder="http://localhost:11434/v1"
              onChange={(e) => setForm({ ...form, base_url: e.target.value })}
              className="mt-1 block w-full rounded-md border border-border bg-surface-2 px-2 py-1.5 text-[12px] text-text" />
          </label>
        )}
        <label className="col-span-12 md:col-span-2 flex items-center gap-2 text-[11px] text-dim">
          <input type="checkbox" checked={form.is_default}
            onChange={(e) => setForm({ ...form, is_default: e.target.checked })} />
          Make this the default model
        </label>
      </div>
      {msg && <p className="mt-2 text-[12px] text-warn">{msg}</p>}
      <button onClick={save} disabled={!me}
        className="mt-3 rounded-lg bg-accent px-4 py-1.5 text-[12px] font-semibold text-[#0b0f1a] disabled:opacity-40">
        Add model config
      </button>

      {cat && cat.configs.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {cat.configs.map((c) => (
            <li key={c.id} className="glass-tile flex items-center justify-between px-3 py-2 text-[12px]">
              <span className="flex items-center gap-2">
                <span className="font-medium">{c.label}</span>
                {c.key_masked && <code className="text-[10px] text-faint">{c.key_masked}</code>}
                {c.is_default && <StatusBadge tone="pos">default</StatusBadge>}
              </span>
              <button
                onClick={async () => { await apiPost(`/api/v1/settings/models/${c.id}/default`, {}); load(); }}
                className="text-[11px] text-accent hover:underline">set default</button>
            </li>
          ))}
        </ul>
      )}
      {cat && <p className="mt-2 text-[10px] text-faint">{cat.note}</p>}
    </SectionCard>
  );
}

/* ── demo/live data switch ── */

function DemoToggle({ me }: { me: Me | null }) {
  const [cfg, setCfg] = useState<{ demo_fixtures: boolean } | null>(null);
  const [flag, setFlag] = useState<boolean | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    apiGet<{ demo_fixtures: boolean }>("/api/v1/settings/env")
      .then((d) => setCfg(d)).catch(() => {});
  }, []);

  const current = flag ?? cfg?.demo_fixtures ?? true;
  const toggle = async () => {
    try {
      const r = await apiPost<{ value: boolean }>(
        `/api/v1/settings/flags/demo_fixtures?value=${!current}`, {});
      setFlag(r.value);
      setMsg(r.value ? "Demo mode ON — empty sections show labeled examples."
                     : "Live mode — empty sections show nothing (honest).");
    } catch (e) {
      setMsg(e instanceof ApiError && e.status === 403
        ? "Admin permission required." : (e as Error).message);
    }
  };
  return (
    <SectionCard title="Demo / live data" className="rise">
      <div className="flex items-center justify-between">
        <p className="max-w-md text-[12px] text-dim">
          When a section has no real data yet, <b>demo mode</b> fills it
          with a clearly-labeled example. <b>Live mode</b> shows honest
          empty states instead. Real sections are real either way.
        </p>
        <button onClick={toggle} disabled={!me}
          className={`relative h-6 w-11 shrink-0 rounded-full transition ${current ? "bg-warn/50" : "bg-pos/50"} ${!me && "opacity-40"}`}
          aria-label="Toggle demo data">
          <span className={`absolute top-0.5 size-5 rounded-full bg-white transition-all ${current ? "left-0.5" : "left-[22px]"}`} />
        </button>
      </div>
      <p className="mt-1 text-[11px] text-faint">
        currently: {current ? "demo fills ON" : "live only"} {msg && `· ${msg}`}
      </p>
    </SectionCard>
  );
}

/** Editable data-source key row — input expands inline, saves
 * server-side (masked forever after). */
function KeyRow({ k, onSaved }: { k: EnvKey; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const save = async () => {
    try {
      await apiPost("/api/v1/settings/keys", { key: k.var.split(" / ")[0], value: val });
      setVal(""); setEditing(false); setMsg(null); onSaved();
    } catch (e) {
      setMsg(e instanceof ApiError && e.status === 403
        ? "Admin required" : (e as Error).message);
    }
  };
  return (
    <li className="glass-tile px-3 py-2">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1">
          <div className="flex items-center gap-2 text-[12px]">
            <span className="font-medium">{k.label}</span>
            <code className="text-[10px] text-faint">{k.var}</code>
            <StatusBadge tone={k.set ? "pos" : "warn"}>
              {k.set ? "set" : "not set"}
            </StatusBadge>
          </div>
          <div className="mt-0.5 text-[11px] text-dim">
            {k.unlocks} · <span className="text-faint">{k.where}</span>
          </div>
          {editing && (
            <div className="mt-2 flex gap-2">
              <input type="password" value={val} autoComplete="new-password"
                placeholder="paste key — stored server-side"
                onChange={(e) => setVal(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && save()}
                className="w-64 rounded-md border border-border bg-surface-2 px-2 py-1 text-[12px] text-text" />
              <button onClick={save}
                className="rounded-md bg-accent px-3 text-[11px] font-semibold text-[#0b0f1a]">save</button>
            </div>
          )}
          {msg && <div className="mt-1 text-[11px] text-warn">{msg}</div>}
        </div>
        <button onClick={() => setEditing((x) => !x)}
          className="shrink-0 text-[11px] text-accent hover:underline">
          {editing ? "cancel" : k.set ? "update" : "set"}
        </button>
      </div>
    </li>
  );
}


/* ── notification channels — detailed per-channel setup ── */

interface NotifCfg {
  id: string; channel: string; target: string; enabled: boolean;
  min_severity: string;
}
interface NotifField { name: string; label: string; kind: "target" | "extra" | "secret"; default?: string }
interface NotifSpec { id: string; label: string; group: string;
  target_label: string; fields: NotifField[]; setup: string[]; note: string }
interface NotifCatalog { channels: NotifCfg[]; specs: NotifSpec[]; note: string }

function Notifications({ me }: { me: Me | null }) {
  const [cat, setCat] = useState<NotifCatalog | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [channel, setChannel] = useState("email_resend");
  const [vals, setVals] = useState<Record<string, string>>({});
  const [sev, setSev] = useState("critical");
  const load = () => apiGet<NotifCatalog>("/api/v1/settings/notifications")
    .then(setCat).catch(() => {});
  useEffect(() => { load(); }, []);

  const spec = cat?.specs.find((s) => s.id === channel);

  const add = async () => {
    setMsg(null);
    try {
      // secrets first — they land in the server-side store
      for (const f of spec?.fields ?? []) {
        if (f.kind === "secret" && vals[f.name]) {
          await apiPost("/api/v1/settings/keys",
            { key: f.name, value: vals[f.name] });
        }
      }
      const target = spec?.fields.find((f) => f.kind === "target");
      const extra = Object.fromEntries(
        (spec?.fields ?? []).filter((f) => f.kind === "extra" && vals[f.name])
          .map((f) => [f.name, vals[f.name]]));
      if (vals["from"]) extra["from"] = vals["from"];
      await apiPost("/api/v1/settings/notifications", {
        channel, target: target ? vals[target.name] : "",
        min_severity: sev, extra,
      });
      setVals({}); load();
      setMsg(`✓ ${spec?.label} channel added`);
    } catch (e) {
      setMsg(e instanceof ApiError && e.status === 403
        ? "Admin required" : (e as Error).message);
    }
  };

  return (
    <SectionCard title="Notifications" className="rise"
      action={<a href="/docs#keys" className="text-accent text-[11px] hover:underline">setup →</a>}>
      <p className="mb-3 text-[11px] text-dim">
        Alerts dispatch to every enabled channel at or above its
        severity floor. Credentials are stored server-side and masked —
        each field below saves when you press Add.
      </p>
      <div className="mb-3 flex flex-wrap gap-1.5">
        {cat?.specs.map((s) => (
          <button key={s.id} onClick={() => { setChannel(s.id); setVals({}); }}
            className={`rounded-full border px-3 py-1 text-[11px] transition ${
              channel === s.id
                ? "border-accent bg-accent/10 font-semibold text-accent"
                : "border-border text-dim hover:text-text"}`}>
            {s.label}
          </button>
        ))}
      </div>

      {spec && (
        <div className="glass-tile mb-3 p-3">
          <ol className="mb-2 list-decimal pl-4 text-[11px] leading-relaxed text-dim">
            {spec.setup.map((step, i) => <li key={i}>{step}</li>)}
          </ol>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            {spec.fields.map((f) => (
              <label key={f.name} className="text-[11px] text-dim">
                {f.label}
                {f.kind === "secret" && (
                  <span className="ml-1 text-[9px] text-faint">(stored masked)</span>)}
                <input
                  type={f.kind === "secret" && !f.name.includes("HOST") ? "password" : "text"}
                  value={vals[f.name] ?? f.default ?? ""}
                  placeholder={f.default}
                  autoComplete="new-password"
                  onChange={(e) => setVals({ ...vals, [f.name]: e.target.value })}
                  className="mt-1 block w-full rounded-md border border-border bg-surface-2 px-2 py-1.5 text-[12px] text-text" />
              </label>
            ))}
            <label className="text-[11px] text-dim">Min severity
              <select value={sev} onChange={(e) => setSev(e.target.value)}
                className="mt-1 block w-full rounded-md border border-border bg-surface-2 px-2 py-1.5 text-[12px] text-text">
                <option value="info">info+</option>
                <option value="warning">warning+</option>
                <option value="critical">critical only</option>
              </select>
            </label>
          </div>
          <button onClick={add} disabled={!me}
            className="mt-3 rounded-lg bg-accent px-4 py-1.5 text-[12px] font-semibold text-[#0b0f1a] disabled:opacity-40">
            Add channel
          </button>
          <p className="mt-2 text-[10px] text-faint">{spec.note}</p>
        </div>
      )}
      {msg && <p className="mb-2 text-[12px] text-warn">{msg}</p>}

      {cat && cat.channels.length > 0 && (
        <ul className="space-y-1.5">
          {cat.channels.map((c) => (
            <li key={c.id} className="glass-tile flex items-center justify-between px-3 py-2 text-[12px]">
              <span className="flex items-center gap-2">
                <StatusBadge tone="info">{c.channel}</StatusBadge>
                <span className="num">{c.target}</span>
                <span className="text-[10px] text-faint">{c.min_severity}+</span>
                {!c.enabled && <StatusBadge tone="warn">off</StatusBadge>}
              </span>
              <span className="flex gap-2">
                <button onClick={async () => {
                    const r = await apiPost<{ok: boolean; error?: string}>(
                      `/api/v1/settings/notifications/${c.id}/test`, {});
                    setMsg(r.ok ? `✓ test sent to ${c.channel}` : `✗ ${c.channel}: ${r.error}`);
                  }} className="text-[11px] text-accent hover:underline">test</button>
                <button onClick={async () => { await apiPost(`/api/v1/settings/notifications/${c.id}/toggle`, {}); load(); }}
                  className="text-[11px] text-dim hover:text-text">{c.enabled ? "disable" : "enable"}</button>
                <button onClick={async () => { await apiDelete(`/api/v1/settings/notifications/${c.id}`); load(); }}
                  className="text-[11px] text-neg hover:underline">remove</button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}

/* ── account: change password + user admin ── */

interface UserRow { id: string; email: string; display_name?: string | null; roles: string[] }

function AccountSecurity({ me }: { me: Me | null }) {
  const [pw, setPw] = useState({ old: "", new: "" });
  const [msg, setMsg] = useState<string | null>(null);
  const [users, setUsers] = useState<UserRow[]>([]);
  const [nu, setNu] = useState({ email: "", password: "", name: "", role: "viewer" });
  const isAdmin = me?.permissions?.includes("admin:*");

  const load = () => {
    if (isAdmin) apiGet<UserRow[]>("/api/v1/auth/users").then(setUsers).catch(() => {});
  };
  useEffect(load, [isAdmin]);

  return (
    <SectionCard title="Account & users" className="rise">
      {me && (
        <div className="mb-4">
          <div className="mb-1 text-[12px] font-medium">Change your password</div>
          <div className="flex flex-wrap gap-2">
            <input type="password" placeholder="current password" value={pw.old}
              autoComplete="current-password"
              onChange={(e) => setPw({ ...pw, old: e.target.value })}
              className="w-48 rounded-md border border-border bg-surface-2 px-2 py-1.5 text-[12px] text-text" />
            <input type="password" placeholder="new password (10+ chars)" value={pw.new}
              autoComplete="new-password"
              onChange={(e) => setPw({ ...pw, new: e.target.value })}
              className="w-48 rounded-md border border-border bg-surface-2 px-2 py-1.5 text-[12px] text-text" />
            <button onClick={async () => {
                setMsg(null);
                try { await apiPost("/api/v1/auth/change-password", { old_password: pw.old, new_password: pw.new }); setMsg("✓ password changed"); setPw({ old: "", new: "" }); }
                catch (e) { setMsg((e as Error).message); }
              }}
              className="rounded-lg bg-accent px-3 py-1.5 text-[12px] font-semibold text-[#0b0f1a]">Change</button>
          </div>
          {msg && <p className="mt-1 text-[11px] text-warn">{msg}</p>}
        </div>
      )}

      {isAdmin && (
        <div>
          <div className="mb-1 text-[12px] font-medium">Users <span className="text-faint">(admin)</span></div>
          <div className="mb-2 flex flex-wrap gap-2">
            <input placeholder="email" value={nu.email}
              onChange={(e) => setNu({ ...nu, email: e.target.value })}
              className="w-48 rounded-md border border-border bg-surface-2 px-2 py-1.5 text-[12px] text-text" />
            <input placeholder="display name" value={nu.name}
              onChange={(e) => setNu({ ...nu, name: e.target.value })}
              className="w-40 rounded-md border border-border bg-surface-2 px-2 py-1.5 text-[12px] text-text" />
            <input type="password" placeholder="temp password" value={nu.password}
              onChange={(e) => setNu({ ...nu, password: e.target.value })}
              className="w-40 rounded-md border border-border bg-surface-2 px-2 py-1.5 text-[12px] text-text" />
            <select value={nu.role} onChange={(e) => setNu({ ...nu, role: e.target.value })}
              className="rounded-md border border-border bg-surface-2 px-2 py-1.5 text-[12px] text-text">
              <option value="viewer">viewer</option>
              <option value="admin">admin</option>
            </select>
            <button onClick={async () => {
                setMsg(null);
                try { await apiPost("/api/v1/auth/users", { email: nu.email, password: nu.password, display_name: nu.name || null, role: nu.role }); setNu({ email: "", password: "", name: "", role: "viewer" }); load(); setMsg("✓ user created"); }
                catch (e) { setMsg((e as Error).message); }
              }}
              className="rounded-lg bg-accent px-3 py-1.5 text-[12px] font-semibold text-[#0b0f1a]">Create</button>
          </div>
          <ul className="space-y-1">
            {users.map((u) => (
              <li key={u.id} className="glass-tile flex items-center justify-between px-3 py-1.5 text-[12px]">
                <span>{u.email} {u.display_name && <span className="text-faint">· {u.display_name}</span>}</span>
                <span className="text-[10px] text-dim">{u.roles.join(", ")}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </SectionCard>
  );
}
