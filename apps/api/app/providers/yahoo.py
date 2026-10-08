"""Yahoo Finance chart adapter — free daily OHLCV (delayed EOD).

Public chart endpoint; data is real but unofficial/delayed — records
carry source='yahoo' and provider is marked delayed. No credentials.
"""

from datetime import datetime, timezone

import httpx

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
BASE = "https://query1.finance.yahoo.com/v8/finance/chart"

# Yahoo timeseries type → canonical `yahoo:` concept. Only metrics the
# screening/valuation engines actually consume — the fallback's job is
# coverage of the same canonical metrics, not a data dump.
FUNDAMENTAL_TYPES: dict[str, str] = {
    "annualTotalRevenue": "yahoo:TotalRevenue",
    "annualNetIncome": "yahoo:NetIncome",
    "annualOperatingIncome": "yahoo:OperatingIncome",
    "annualOperatingCashFlow": "yahoo:OperatingCashFlow",
    "annualCapitalExpenditure": "yahoo:CapitalExpenditure",
    "annualStockholdersEquity": "yahoo:StockholdersEquity",
    "annualLongTermDebt": "yahoo:LongTermDebt",
    "annualTotalDebt": "yahoo:TotalDebt",
    "annualCurrentDebt": "yahoo:CurrentDebt",
    "annualCashAndCashEquivalents": "yahoo:CashAndCashEquivalents",
    "annualInterestExpense": "yahoo:InterestExpense",
    "annualInterestExpenseNonOperating": "yahoo:InterestExpenseNonOperating",
    "annualTaxProvision": "yahoo:TaxProvision",
    "annualPretaxIncome": "yahoo:PretaxIncome",
    "annualDepreciationAndAmortization": "yahoo:DepreciationAndAmortization",
    "annualDepreciationAmortization": "yahoo:DepreciationAndAmortization",
    "annualAccountsReceivable": "yahoo:AccountsReceivable",
    "annualCurrentAssets": "yahoo:CurrentAssets",
    "annualCurrentLiabilities": "yahoo:CurrentLiabilities",
    "annualBasicEPS": "yahoo:BasicEPS",
    "annualDilutedEPS": "yahoo:DilutedEPS",
    "annualBasicAverageShares": "yahoo:BasicAverageShares",
    "annualDilutedAverageShares": "yahoo:DilutedAverageShares",
    "annualCashDividendsPaid": "yahoo:CashDividendsPaid",
    "annualDividendsPaid": "yahoo:DividendsPaid",
    "trailingMarketCap": "yahoo:TrailingMarketCap",
}


class YahooAdapter:
    name = "yahoo"
    kind = "market"

    async def fetch_daily(self, symbol: str, range_: str = "2y",
                          start: str | None = None) -> list[dict]:
        # range='max' makes Yahoo downsample to sparse monthly anchors
        # timestamped at month-start — NOT daily bars. Deep history must
        # be requested via explicit period1/period2.
        params: dict = {"interval": "1d"}
        if start:
            p1 = int(datetime.fromisoformat(start)
                     .replace(tzinfo=timezone.utc).timestamp())
            params["period1"] = p1
            params["period2"] = int(datetime.now(tz=timezone.utc)
                                    .timestamp())
        else:
            params["range"] = range_
        async with httpx.AsyncClient(timeout=90, headers=UA) as c:
            r = await c.get(f"{BASE}/{symbol.upper()}", params=params)
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

    async def fundamentals_timeseries(
        self, symbol: str, types: list[str] | None = None,
        years: int = 10,
    ) -> dict[str, dict]:
        """Yahoo fundamentals-timeseries — annual financials keyed by
        type. Returns {type: {value, asOfDate, currencyCode}} for the
        LATEST annual point per type plus a `{type}__series` history —
        enough to fill fundamentals for issuers with no SEC XBRL
        coverage (some ADRs/foreign listings) — clearly sourced
        'yahoo', never mixed into EDGAR rows."""
        types = types or list(FUNDAMENTAL_TYPES)
        now = datetime.now(tz=timezone.utc)
        p2 = int(now.timestamp())
        p1 = int(now.replace(year=now.year - years).timestamp())
        async with httpx.AsyncClient(timeout=30, headers=UA) as c:
            r = await c.get(
                "https://query1.finance.yahoo.com/ws/"
                f"fundamentals-timeseries/v1/finance/timeseries/"
                f"{symbol.upper()}",
                params={"type": ",".join(types),
                        "period1": p1, "period2": p2})
            r.raise_for_status()
        out: dict[str, dict] = {}
        for res in (r.json().get("timeseries", {}) or {}).get("result", []):
            mtype = ((res.get("meta") or {}).get("type") or [None])[0]
            if not mtype:
                continue
            rows = res.get(mtype) or []
            series = []
            for row in rows:
                rv = row.get("reportedValue") or {}
                if rv.get("raw") is None:
                    continue
                series.append({
                    "value": float(rv["raw"]),
                    "asOfDate": row.get("asOfDate"),
                    "periodType": row.get("periodType"),
                    "currencyCode": rv.get("currencyCode")
                                    or row.get("currencyCode"),
                })
            if series:
                out[mtype] = series[-1]
                out[mtype + "__series"] = series
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
