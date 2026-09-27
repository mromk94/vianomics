const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function apiGet<T>(path: string, timeoutMs = 8000): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_URL}${path}`, { signal: ctrl.signal });
    if (!res.ok) {
      throw new ApiError(res.status, `API ${res.status} on ${path}`);
    }
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
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
  };
  split: { investment_pct: number | null; trading_pct: number | null };
  regime: {
    economic_regime: string | null;
    market_regime: string | null;
    fear_greed: number | null;
    vix: number | null;
  };
  risk: {
    drawdown_pct: number | null;
    drawdown_limit_pct: number;
    max_sector_pct: number | null;
    sector_limit_pct: number;
    max_position_pct: number | null;
    position_limit_pct: number;
    warnings: string[];
  };
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
