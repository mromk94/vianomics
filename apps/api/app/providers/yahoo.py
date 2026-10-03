"""Yahoo Finance chart adapter — free daily OHLCV (delayed EOD).

Public chart endpoint; data is real but unofficial/delayed — records
carry source='yahoo' and provider is marked delayed. No credentials.
"""

from datetime import datetime, timezone

import httpx

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
BASE = "https://query1.finance.yahoo.com/v8/finance/chart"


class YahooAdapter:
    name = "yahoo"
    kind = "market"

    async def fetch_daily(self, symbol: str, range_: str = "2y") -> list[dict]:
        async with httpx.AsyncClient(timeout=30, headers=UA) as c:
            r = await c.get(f"{BASE}/{symbol.upper()}",
                            params={"range": range_, "interval": "1d"})
            r.raise_for_status()
        result = r.json()["chart"]["result"][0]
        ts = result.get("timestamp") or []
        q = result["indicators"]["quote"][0]
        out = []
        for i, t in enumerate(ts):
            if q["close"][i] is None:
                continue
            out.append({
                "observed_at": datetime.fromtimestamp(t, tz=timezone.utc),
                "open": q["open"][i],
                "high": q["high"][i],
                "low": q["low"][i],
                "close": q["close"][i],
                "volume": q["volume"][i] or 0,
            })
        return out

    async def fetch_quote(self, symbol: str) -> dict | None:
        """Chart-API meta → quote snapshot. Uses the same free endpoint
        as bars — the v7 quote API needs a crumb/cookie, the chart meta
        does not. Fields present vary by session state (regular vs
        extended hours)."""
        async with httpx.AsyncClient(timeout=20, headers=UA) as c:
            r = await c.get(f"{BASE}/{symbol.upper()}",
                            params={"range": "5d", "interval": "1d"})
            r.raise_for_status()
        result = (r.json().get("chart") or {}).get("result") or []
        if not result:
            return None
        meta = result[0].get("meta") or {}
        price = meta.get("regularMarketPrice")
        bid = meta.get("bid") or price
        ask = meta.get("ask") or price
        return {
            "mid": price,
            "bid": bid,
            "ask": ask,
            "prev_close": meta.get("previousClose")
            or meta.get("chartPreviousClose"),
            "day_open": meta.get("regularMarketOpen")
            or meta.get("currentTradingPeriod", {})
                      .get("regular", {}).get("open"),
            "market_state": meta.get("marketState"),
        }
