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

export async function apiGet<T>(path: string, timeoutMs = 8000): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
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
  } finally {
    clearTimeout(timer);
  }
}

export async function apiPost<T>(
  path: string,
  body: unknown,
  timeoutMs = 8000,
): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
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
  } finally {
    clearTimeout(timer);
  }
}

export async function apiPut<T>(
  path: string,
  body: unknown,
  timeoutMs = 8000,
): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
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
