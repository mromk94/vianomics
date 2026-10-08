"""Alpaca market-data adapter — data.alpaca.markets (separate host
from the trading API). Free tier is the IEX feed; SIP needs the paid
data subscription — `feed` is a constructor arg so callers stay
honest about which tape a number came from.

Keys: ALPACA_API_KEY / ALPACA_SECRET_KEY (env or SecretStore — the
latter is loaded into env at startup).
"""

import os
from typing import Any

from app.providers.base import HttpAdapter, ProviderConfigError


class AlpacaAdapter(HttpAdapter):
    key = "alpaca"
    kind = "market"
    base_url = "https://data.alpaca.markets"
    trading_base = "https://api.alpaca.markets"   # assets live here
    rps = 3.0  # free tier ~200 req/min

    @staticmethod
    def _asym(symbol: str) -> str:
        """Internal tickers are stored in canonical dash form
        (BRK-B — Yahoo/SEC convention); Alpaca's API wants the dot
        form for share classes."""
        return symbol.upper().replace("-", ".")

    def __init__(self, api_key: str | None = None,
                 secret_key: str | None = None,
                 feed: str | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.api_key = api_key or os.environ.get("ALPACA_API_KEY")
        self.secret_key = (secret_key
                           or os.environ.get("ALPACA_SECRET_KEY"))
        # ALPACA_FEED=sip on the paid plan — every stored quote/bar
        # carries source='alpaca'; the feed name rides in provider
        # capabilities so the tape used is auditable
        self.feed = feed or os.environ.get("ALPACA_FEED", "iex")

    def _headers(self) -> dict[str, str]:
        if not (self.api_key and self.secret_key):
            raise ProviderConfigError(
                "alpaca: ALPACA_API_KEY + ALPACA_SECRET_KEY "
                "not configured")
        return {"APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.secret_key}

    def capabilities(self) -> dict[str, Any]:
        return {
            "asset_classes": ["equity", "etf"],
            "data": ["eod_ohlcv", "intraday", "quotes", "trades",
                     "snapshots", "news"],
            "feed": self.feed,
            "requires_credentials": True,
            "configured": bool(self.api_key and self.secret_key),
        }

    async def healthcheck(self) -> bool:
        resp = await self._get(
            f"{self.base_url}/v2/stocks/AAPL/trades/latest",
            params={"feed": self.feed}, headers=self._headers())
        return resp.status_code == 200

    async def bars(self, symbol: str, start: str, end: str,
                   timeframe: str = "1Day",
                   limit: int = 10000) -> list[dict]:
        """→ [{t,o,h,l,c,v,n,vw}] raw Alpaca bars (paged)."""
        out: list[dict] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {
                "start": start, "end": end, "timeframe": timeframe,
                "feed": self.feed, "limit": limit,
                "adjustment": "raw"}
            if page_token:
                params["page_token"] = page_token
            resp = await self._get(
                f"{self.base_url}/v2/stocks/{self._asym(symbol)}/bars",
                params=params, headers=self._headers())
            resp.raise_for_status()
            d = resp.json()
            out.extend(d.get("bars") or [])
            page_token = d.get("next_page_token")
            if not page_token:
                return out

    async def assets(self, status: str = "active",
                     asset_class: str = "us_equity") -> list[dict]:
        """Full tradable-asset listing (trading API, not the data
        host). One unpaginated call returns ~10k rows — the market
        universe the screener's discovery pool should draw from."""
        resp = await self._get(
            f"{self.trading_base}/v2/assets",
            params={"status": status, "asset_class": asset_class},
            headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    async def latest_quote(self, symbol: str) -> dict | None:
        """NBBO-ish quote {bp,bs,ap,as,t} — IEX tape on free tier."""
        resp = await self._get(
            f"{self.base_url}/v2/stocks/{self._asym(symbol)}"
            "/quotes/latest",
            params={"feed": self.feed}, headers=self._headers())
        if resp.status_code != 200:
            return None
        return resp.json().get("quote")

    async def latest_trade(self, symbol: str) -> dict | None:
        """Last trade {p,s,t,x}."""
        resp = await self._get(
            f"{self.base_url}/v2/stocks/{self._asym(symbol)}"
            "/trades/latest",
            params={"feed": self.feed}, headers=self._headers())
        if resp.status_code != 200:
            return None
        return resp.json().get("trade")

    async def snapshot(self, symbol: str) -> dict | None:
        """{latestTrade,latestQuote,dailyBar,prevDailyBar,minuteBar} —
        one call carries everything a quote row needs plus the day's
        open and prior close."""
        resp = await self._get(
            f"{self.base_url}/v2/stocks/{self._asym(symbol)}/snapshot",
            params={"feed": self.feed}, headers=self._headers())
        if resp.status_code != 200:
            return None
        return resp.json()
