"""Stooq adapter — free delayed EOD OHLCV (public CSV).

Data is real but delayed/end-of-day — records carry
`source_ref='stooq:eod'` and provider health marks it delayed, not
streaming. No credentials needed.
"""

import csv
import io
from datetime import date, datetime, timezone

import httpx

STOOQ_URL = "https://stooq.com/q/d/l/"


class StooqAdapter:
    name = "stooq"
    kind = "market"

    async def fetch_daily(self, symbol: str,
                          start: date | None = None) -> list[dict]:
        """US tickers → '{sym}.us'. Returns daily OHLCV dicts."""
        params = {"s": f"{symbol.lower()}.us", "i": "d"}
        if start:
            params["d1"] = start.strftime("%Y%m%d")
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(STOOQ_URL, params=params)
            r.raise_for_status()
        text = r.text.strip()
        if "Exceeded the daily hits limit" in text or len(text) < 50:
            return []
        out = []
        for row in csv.DictReader(io.StringIO(text)):
            if not row.get("Close") or row["Close"] == "N/D":
                continue
            d = date.fromisoformat(row["Date"])
            out.append({
                "date": d,
                "observed_at": datetime.combine(
                    d, datetime.min.time(), tzinfo=timezone.utc),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row.get("Volume") or 0),
            })
        return out
