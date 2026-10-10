"""StockRow fundamentals adapter — stockrow.com/api/v1 (paid).

Serves two shapes:
- /companies/{ticker}/metrics/{slug} — point-in-time metric history
  (annual/quarterly/ttm/latest), up to ~10y of annual points.
- /metrics/latest — a grid of latest metrics across up to 100
  tickers × 25 slugs per call — the batch screen path.
- /indicators — the metric dictionary (slug → name/unit/periods).

Auth: `?key=` query param — STOCKROW_API_KEY env or SecretStore
(loaded into env at startup, same convention as Alpaca).
"""

import os
from datetime import date
from typing import Any

from app.providers.base import HttpAdapter, ProviderConfigError


class StockRowAdapter(HttpAdapter):
    key = "stockrow"
    kind = "fundamentals"
    base_url = "https://stockrow.com/api/v1"
    rps = 1.0  # conservative — paid tier limits unpublished

    def __init__(self, api_key: str | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.api_key = api_key or os.environ.get("STOCKROW_API_KEY")

    def _params(self, extra: dict[str, Any] | None = None) -> dict:
        if not self.api_key:
            raise ProviderConfigError(
                "stockrow: STOCKROW_API_KEY not configured")
        return {"key": self.api_key, **(extra or {})}

    def capabilities(self) -> dict[str, Any]:
        return {
            "asset_classes": ["equity"],
            "data": ["metric_history", "metric_grid", "statements",
                     "growth_cagr_10y"],
            "requires_credentials": True,
            "configured": bool(self.api_key),
        }

    async def healthcheck(self) -> bool:
        resp = await self._get(f"{self.base_url}/companies/AAPL"
                               "/metrics/revenue",
                               params=self._params(
                                   {"period": "latest", "limit": 1}))
        return resp.status_code == 200

    async def metric_history(self, ticker: str, slug: str,
                             period: str = "annual",
                             limit: int = 120) -> list[dict]:
        """→ [{period_end, value}] newest-first. Empty list on 404
        (unknown ticker/slug) — caller treats it as no coverage,
        never as a zero."""
        try:
            resp = await self._get(
                f"{self.base_url}/companies/{ticker.upper()}"
                f"/metrics/{slug}",
                params=self._params(
                    {"period": period, "limit": min(limit, 120)}))
        except Exception as e:
            # no-coverage statuses → empty series; throttles and 5xx
            # stay retryable ProviderErrors
            if any(f"HTTP {c}" in str(e)
                   for c in ("400", "401", "403", "404", "422")):
                return []
            raise
        try:
            data = resp.json()
        except ValueError:
            return []
        rows = data.get("data") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return []
        return [r for r in rows
                if isinstance(r, dict) and r.get("period_end")
                and r.get("value") is not None]

    async def annual_series(self, ticker: str, slug: str,
                            limit: int = 12) -> dict[date, float]:
        """Annual metric history → {period_end: value}, oldest→newest
        ready for first→last CAGR. `limit=12` covers a 10-year span
        with headroom for fiscal-year drift."""
        rows = await self.metric_history(
            ticker, slug, period="annual", limit=limit)
        out: dict[date, float] = {}
        for r in rows:
            try:
                out[date.fromisoformat(str(r["period_end"])[:10])] = \
                    float(r["value"])
            except (TypeError, ValueError):
                continue
        return dict(sorted(out.items()))

    async def metrics_latest(self, tickers: list[str],
                             slugs: list[str]) -> dict:
        """Latest-metric grid — up to 100 tickers × 25 slugs per call
        (batch screen path). → provider payload verbatim."""
        resp = await self._get(
            f"{self.base_url}/metrics/latest",
            params=self._params({
                "symbols": ",".join(t.upper() for t in tickers[:100]),
                "metrics": ",".join(slugs[:25])}))
        try:
            return resp.json()
        except ValueError:
            return {}
