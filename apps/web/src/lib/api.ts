// API base resolution: env var wins; on a deployed host without it we
// fall back to the production API rather than localhost (prevents
// "Cannot reach API" when a domain alias lacks the env var).
function resolveApiUrl(): string {
  const env = process.env.NEXT_PUBLIC_API_URL;
  if (env) return env;
  if (typeof window !== "undefined" &&
      !["localhost", "127.0.0.1"].includes(window.location.hostname)) {
    return "https://vianomics.onrender.com";
  }
  return "http://localhost:8000";
}
const API_URL = resolveApiUrl();

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function authHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const t = window.localStorage.getItem("vaiip-token");
  return t ? { Authorization: `Bearer ${t}` } : {};
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem("vaiip-token");
}

export function setToken(t: string | null) {
  if (t) window.localStorage.setItem("vaiip-token", t);
  else window.localStorage.removeItem("vaiip-token");
  window.dispatchEvent(new Event("vaiip-auth"));
}

/* Timeout tiers — analytical endpoints run multi-table gates and can
   take tens of seconds on a cold DB; giving them the 8s default
   produced opaque "signal is aborted without reason" errors while the
   server was still working (and would log a spurious 200). */
const FAST_MS = 10_000;
const SLOW_MS = 45_000;
const SLOW_PREFIXES = [
  "/api/v1/symbol/", "/api/v1/research/", "/api/v1/committee/",
  "/api/v1/risk/eligibility", "/api/v1/risk/pyramid",
  "/api/v1/risk/atr", "/api/v1/risk/sleeve", "/api/v1/dataops/",
  // screener reads materialize a whole run's criteria JSON and share
  // the DB pool with the background screen — the first (uncached)
  // build legitimately exceeds the fast tier under load
  "/api/v1/screener/",
];

function defaultTimeout(path: string): number {
  return SLOW_PREFIXES.some((p) => path.startsWith(p)) ? SLOW_MS : FAST_MS;
}

function abortReason(e: unknown): string | null {
  const name = (e as { name?: string })?.name;
  if (name === "AbortError" || name === "TimeoutError")
    return "request timed out (client abort) — the API may still be working; retry if needed";
  return null;
}

export async function apiGet<T>(path: string, timeoutMs?: number): Promise<T> {
  const ms = timeoutMs ?? defaultTimeout(path);
  let lastErr: unknown;
  // Render restarts (deploys, transient drops) surface as a one-shot
  // network error — one quiet retry with backoff keeps the UI from
  // flashing "Cannot reach the API" on a blip. GETs are idempotent.
  for (let attempt = 0; attempt < 2; attempt++) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), ms);
    try {
      const res = await fetch(`${API_URL}${path}`, {
        signal: ctrl.signal,
        headers: authHeaders(),
      });
      if (!res.ok) {
        if (res.status === 401 && path !== "/api/v1/auth/login") {
          // dead session — drop it and bounce to /login
          window.localStorage.removeItem("vaiip-token");
          window.dispatchEvent(new Event("vaiip:unauth"));
        }
        throw new ApiError(res.status, `API ${res.status} on ${path}`);
      }
      return (await res.json()) as T;
    } catch (e) {
      lastErr = e;
      const retriable = e instanceof ApiError
        ? (e.status === 0 || e.status >= 500)
        : !(e instanceof ApiError);   // network/abort errors
      if (!retriable || attempt === 1) {
        const reason = abortReason(e);
        if (reason) throw new ApiError(0, `${reason} [${path}]`);
        throw e;
      }
      await new Promise((r) => setTimeout(r, 1500));
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastErr;
}

export async function apiPost<T>(
  path: string,
  body: unknown,
  timeoutMs?: number,
): Promise<T> {
  const ms = timeoutMs ?? defaultTimeout(path);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  try {
    const res = await fetch(`${API_URL}${path}`, {
      method: "POST",
      signal: ctrl.signal,
      headers: { "content-type": "application/json", ...authHeaders() },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      if (res.status === 401 && path !== "/api/v1/auth/login") {
        window.localStorage.removeItem("vaiip-token");
        window.dispatchEvent(new Event("vaiip:unauth"));
      }
      const detail = await res.json().catch(() => null);
      const msg =
        typeof detail?.detail === "string"
          ? detail.detail
          : `API ${res.status} on ${path}`;
      throw new ApiError(res.status, msg);
    }
    return (await res.json()) as T;
  } catch (e) {
    const reason = abortReason(e);
    if (reason) throw new ApiError(0, `${reason} [${path}]`);
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

export async function apiPut<T>(
  path: string,
  body: unknown,
  timeoutMs?: number,
): Promise<T> {
  const ms = timeoutMs ?? defaultTimeout(path);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  try {
    const res = await fetch(`${API_URL}${path}`, {
      method: "PUT",
      signal: ctrl.signal,
      headers: { "content-type": "application/json", ...authHeaders() },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      if (res.status === 401 && path !== "/api/v1/auth/login") {
        window.localStorage.removeItem("vaiip-token");
        window.dispatchEvent(new Event("vaiip:unauth"));
      }
      const detail = await res.json().catch(() => null);
      const msg =
        typeof detail?.detail === "string"
          ? detail.detail
          : `API ${res.status} on ${path}`;
      throw new ApiError(res.status, msg);
    }
    return (await res.json()) as T;
  } catch (e) {
    const reason = abortReason(e);
    if (reason) throw new ApiError(0, `${reason} [${path}]`);
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

export async function apiDelete<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "DELETE", headers: authHeaders(),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new ApiError(res.status,
      typeof detail?.detail === "string"
        ? detail.detail : `API ${res.status} on ${path}`);
  }
  return (await res.json()) as T;
}

/* ── Command Center types (mirror apps/api schemas) ── */

export interface CommandCenter {
  generated_at: string;
  demo_sections: string[];
  portfolio: {
    total_value: number | null;
    cash: number | null;
    daily_pnl: number | null;
    daily_pnl_pct: number | null;
    unrealized_pnl: number | null;
    currency: string;
    holdings?: {
      symbol: string; market_value: number;
      unrealized: number | null; weight: number;
      sector: string | null; source: string;
    }[];
  };
  split: { investment_pct: number | null; trading_pct: number | null };
  regime: {
    economic_regime: string | null;
    market_regime: string | null;
    fear_greed: number | null;
    vix: number | null;
    macro_indicators: Record<string, string>;
  };
  risk: {
    drawdown_pct: number | null;
    drawdown_limit_pct: number;
    max_sector_pct: number | null;
    sector_limit_pct: number;
    max_position_pct: number | null;
    position_limit_pct: number;
    var_95: number | null;
    stress_10pct: number | null;
    avg_correlation: number | null;
    warnings: string[];
  };
  cio: {
    rating: string | null;
    confidence: number | null;
    expected_return_pct: number | null;
    mos_pct: number | null;
    risk_veto: string | null;
    book_note?: string | null;
    kill_conditions: string[];
    conflict_note: string | null;
  };
  agents: { agent: string; recommendation: string; score: number | null }[];
  sectors: {
    sector: string;
    weight_pct: number | null;
    day_pct: number | null;
    week_pct: number | null;
    momentum: number | null;
  }[];
  calendar: { title: string; at: string; detail: string | null }[];
  market: {
    symbol: string; name: string; group: string;
    close: number; day_pct: number | null; week_pct: number | null;
    as_of: string;
  }[];
  signals: { kind: string; message: string; danger: boolean }[];
  watchlist: {
    ticker: string;
    name: string | null;
    green_zone_score: number | null;
    last_price: number | null;
    change_pct: number | null;
  }[];
  approvals: {
    id: string;
    ticker: string;
    action: string;
    requested_at: string;
    cio_rating: string | null;
    risk_verdict: string | null;
  }[];
  alerts: {
    id: string;
    severity: "info" | "warning" | "critical";
    message: string;
    source: string;
    at: string;
  }[];
  decisions: {
    id: string;
    ticker: string;
    verdict: string;
    at: string;
    cio_confidence: number | null;
  }[];
  providers: {
    name: string;
    status: "up" | "down" | "degraded" | "unconfigured";
    last_sync: string | null;
    detail: string | null;
  }[];
}
