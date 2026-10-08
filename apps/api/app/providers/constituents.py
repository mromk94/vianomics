"""Index-constituent + most-actives reference lists — credential-free
sources for building the *working* universe (distinct from the 10k-
name Alpaca discovery pool):

- Wikipedia S&P 500 table — symbol, name, GICS sector/sub-industry,
  AND the issuer's SEC CIK (lets EDGAR ingest skip the ticker-map
  lookup entirely)
- Yahoo predefined screener `most_actives` — live top-N by dollar
  volume, catches liquid names outside the index (recent listings,
  high-beta names the desk actually watches)

Symbols are normalized to dash form (BRK-B) — Yahoo and the SEC
ticker map both use dashes; Alpaca adapters translate to dot form at
call time.
"""

from html.parser import HTMLParser
from typing import Any

import httpx

UA = {"User-Agent": "VAIIP-Research admin@vesturs.com"}
WIKI_SP500 = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
YH_SCREENER = ("https://query1.finance.yahoo.com/v1/finance/"
               "screener/predefined/saved")


def canon_symbol(sym: str) -> str:
    """Canonical storage form: uppercase, class shares as dashes
    (Wikipedia 'BRK.B' → 'BRK-B' — matches Yahoo + SEC ticker map)."""
    return sym.strip().upper().replace(".", "-")


class _TableParser(HTMLParser):
    """Extracts <td>/<th> text of the table with id='constituents'."""

    def __init__(self) -> None:
        super().__init__()
        self.in_table = False
        self.in_cell = False
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple]) -> None:
        attrs_d = dict(attrs)
        if tag == "table" and attrs_d.get("id") == "constituents":
            self.in_table = True
        elif self.in_table and tag == "tr":
            self._row = []
        elif self.in_table and tag in ("td", "th"):
            self.in_cell = True
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self.in_table:
            self.in_table = False
        elif self.in_table and tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
        elif self.in_table and tag in ("td", "th") and self.in_cell:
            self.in_cell = False
            if self._row is not None:
                self._row.append("".join(self._cell).strip())

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self._cell.append(data)


async def sp500_constituents(timeout: float = 30.0) -> list[dict[str, Any]]:
    """S&P 500 constituents from the public Wikipedia table —
    {symbol, name, sector, sub_industry, cik}. The CIK column is the
    issuer's SEC identity — fundamentals ingest keys off it
    directly."""
    async with httpx.AsyncClient(timeout=timeout, headers=UA) as c:
        r = await c.get(WIKI_SP500)
        r.raise_for_status()
    p = _TableParser()
    p.feed(r.text)
    out: list[dict[str, Any]] = []
    for row in p.rows:
        # header + footer rows don't carry 8 columns of data
        if len(row) < 7 or row[0].lower() in ("symbol",):
            continue
        sym = canon_symbol(row[0])
        if not sym or not sym.replace("-", "").isalnum():
            continue
        cik = row[6].strip().lstrip("0") or "0"
        out.append({
            "symbol": sym,
            "name": row[1].strip() or sym,
            "sector": row[2].strip() or None,
            "sub_industry": row[3].strip() or None,
            "cik": cik if cik.isdigit() and cik != "0" else None,
            "index": "sp500",
        })
    return out


async def yahoo_most_actives(
    count: int = 250, timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """Top-N most-active US-listed names by volume — the live 'what
    the market is trading' list. {symbol, name, quote_type, volume}."""
    async with httpx.AsyncClient(timeout=timeout, headers=UA) as c:
        r = await c.get(YH_SCREENER, params={
            "scrIds": "most_actives", "count": count,
            "formatted": "false"})
        r.raise_for_status()
    result = (r.json().get("finance", {}).get("result") or [])
    out: list[dict[str, Any]] = []
    for q in (result[0].get("quotes") if result else []) or []:
        sym = canon_symbol(q.get("symbol") or "")
        if not sym or len(sym) > 12 or not sym.replace("-", "").isalnum():
            continue
        qt = (q.get("quoteType") or "").upper()
        out.append({
            "symbol": sym,
            "name": (q.get("shortName") or q.get("longName")
                     or sym),
            "sector": None,
            "sub_industry": None,
            "cik": None,
            "index": "most_active",
            "asset_class": ("etf" if qt == "ETF"
                            else "equity" if qt == "EQUITY" else None),
            "exchange": q.get("fullExchangeName"),
            "volume": q.get("regularMarketVolume"),
        })
    return out
