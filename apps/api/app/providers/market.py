"""Configurable market-data adapter — Tiingo implementation first.

The concrete provider is selected by MARKET_PROVIDER env so swapping
Tiingo for FMP/etc. later doesn't touch callers. Requires an API key;
unconfigured → ProviderConfigError (surfaced as provider status
'unconfigured', never silently faked).
"""

import os
from typing import Any

from app.providers.base import (
    HttpAdapter,
    ProviderConfigError,
)


class TiingoAdapter(HttpAdapter):
    key = "tiingo"
    kind = "market"
    base_url = "https://api.tiingo.com"
    rps = 5.0

    def __init__(self, api_key: str | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.api_key = api_key or os.environ.get("TIINGO_API_KEY")

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ProviderConfigError("tiingo: TIINGO_API_KEY not configured")
        return {"Authorization": f"Token {self.api_key}"}

    def capabilities(self) -> dict[str, Any]:
        return {
            "asset_classes": ["equity"],
            "data": ["eod_ohlcv", "intraday", "metadata"],
            "requires_credentials": True,
            "configured": bool(self.api_key),
        }

    async def healthcheck(self) -> bool:
        resp = await self._get(
            f"{self.base_url}/api/test", headers=self._headers()
        )
        return resp.status_code == 200

    async def eod(self, ticker: str, start: str, end: str) -> list[dict]:
        resp = await self._get(
            f"{self.base_url}/tiingo/daily/{ticker.lower()}/prices",
            params={"startDate": start, "endDate": end},
            headers=self._headers(),
        )
        return resp.json()

    async def meta(self, ticker: str) -> dict:
        resp = await self._get(
            f"{self.base_url}/tiingo/daily/{ticker.lower()}",
            headers=self._headers(),
        )
        return resp.json()

    async def intraday(self, ticker: str, start_date: str,
                       resample_freq: str = "30min") -> list[dict]:
        """IEX intraday bars — /iex/{ticker}/prices resampled server-
        side (1min|5min|15min|30min|1hour|4hour). Returns
        [{date, open, high, low, close, volume}] recent ~30 days max."""
        resp = await self._get(
            f"{self.base_url}/iex/{ticker.lower()}/prices",
            params={
                "startDate": start_date,
                "resampleFreq": resample_freq,
                "columns": "date,open,high,low,close,volume",
            },
            headers=self._headers(),
        )
        return resp.json()


def get_market_adapter() -> HttpAdapter:
    provider = os.environ.get("MARKET_PROVIDER", "tiingo")
    if provider == "tiingo":
        return TiingoAdapter()
    raise ProviderConfigError(f"unknown MARKET_PROVIDER '{provider}'")


CNN_FG_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
# the canonical 7 indicators CNN publishes (sp125/vix_50 are alternate
# series CNN also emits — excluded)
CNN_FG_COMPONENTS = (
    "market_momentum_sp500", "stock_price_strength",
    "stock_price_breadth", "put_call_options",
    "market_volatility_vix", "junk_bond_demand", "safe_haven_demand")


async def fetch_fear_greed(timeout: float = 10.0) -> dict | None:
    """CNN Fear & Greed Index — the public dataviz JSON endpoint that
    backs cnn.com/markets/fear-and-greed. Returns
    {score, rating, previous_close, components{name: score}} or None.

    The endpoint 418s non-browser clients — needs a UA + referer.
    Failure → None (callers fall back honestly, never fake the index).
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(CNN_FG_URL, headers={
                "User-Agent":
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0 Safari/537.36",
                "Accept": "application/json",
                "Referer": "https://www.cnn.com/",
            })
        if resp.status_code != 200:
            return None
        data = resp.json()
        fg = data.get("fear_and_greed") or {}
        score = fg.get("score")
        if score is None:
            return None
        comps = {}
        for k in CNN_FG_COMPONENTS:
            v = data.get(k)
            if isinstance(v, dict) and v.get("score") is not None:
                comps[k] = float(v["score"])
        return {
            "score": round(float(score), 1),
            "rating": fg.get("rating"),
            "previous_close": fg.get("previous_close"),
            "previous_1_week": fg.get("previous_1_week"),
            "components": comps,
        }
    except Exception:
        return None
