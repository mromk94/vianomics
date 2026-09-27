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


def get_market_adapter() -> HttpAdapter:
    provider = os.environ.get("MARKET_PROVIDER", "tiingo")
    if provider == "tiingo":
        return TiingoAdapter()
    raise ProviderConfigError(f"unknown MARKET_PROVIDER '{provider}'")
