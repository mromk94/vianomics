"""Market-context layer — the instruments that describe the MARKET
itself (indices, volatility, rates proxies, sector ETFs, macro ETFs),
distinct from the tradeable universe.

A Trading OS must read the market before it reads a book: regime
classification, sector rotation, breadth and the Market Intelligence
panel all need these series to exist. They are seeded idempotently
and their bars/quotes refresh through the normal ingestion path.

Context set (all free via Yahoo; FRED series live separately):
- index:   SPY QQQ DIA IWM ^VIX ^TNX ^GSPC — trend / vol / rates
- macro:   GLD SLV TLT UUP USO HYG EFA EEM — commodities, bonds,
           dollar, oil, high-yield credit proxy, ex-US
- sectors: the 11 SPDR sector ETFs — real sector rotation, GICS L1
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.upsert import get_or_create
from app.models.instruments import (
    Instrument,
    InstrumentIdentifier,
    Sector,
)
from app.models.market import MarketQuote, OhlcvBar
from app.providers.yahoo import YahooAdapter

# symbol → (name, asset_class, context group)
CONTEXT_INSTRUMENTS: dict[str, tuple[str, str, str]] = {
    # US equity indices / beta
    "SPY":   ("SPDR S&P 500 ETF", "index", "index"),
    "QQQ":   ("Invesco QQQ — Nasdaq 100", "index", "index"),
    "DIA":   ("SPDR Dow Jones Industrial", "index", "index"),
    "IWM":   ("iShares Russell 2000", "index", "index"),
    "^GSPC": ("S&P 500 Index", "index", "index"),
    # volatility + rates
    "^VIX":  ("CBOE Volatility Index", "index", "volatility"),
    "^TNX":  ("10-Year Treasury Yield ×10", "index", "rates"),
    # macro ETFs
    "GLD":   ("SPDR Gold Shares", "etf", "macro"),
    "SLV":   ("iShares Silver Trust", "etf", "macro"),
    "TLT":   ("iShares 20+ Year Treasury", "etf", "macro"),
    "UUP":   ("Invesco DB US Dollar Index", "etf", "macro"),
    "USO":   ("United States Oil Fund", "etf", "macro"),
    "HYG":   ("iShares High Yield Corporate", "etf", "macro"),
    "EFA":   ("iShares MSCI EAFE", "etf", "macro"),
    "EEM":   ("iShares MSCI Emerging Markets", "etf", "macro"),
    # GICS sector SPDRs — real sector rotation
    "XLC":   ("Communication Services SPDR", "etf", "sector"),
    "XLE":   ("Energy SPDR", "etf", "sector"),
    "XLF":   ("Financial SPDR", "etf", "sector"),
    "XLI":   ("Industrial SPDR", "etf", "sector"),
    "XLK":   ("Technology SPDR", "etf", "sector"),
    "XLP":   ("Consumer Staples SPDR", "etf", "sector"),
    "XLRE":  ("Real Estate SPDR", "etf", "sector"),
    "XLU":   ("Utilities SPDR", "etf", "sector"),
    "XLV":   ("Health Care SPDR", "etf", "sector"),
    "XLY":   ("Consumer Discretionary SPDR", "etf", "sector"),
    "XLB":   ("Materials SPDR", "etf", "sector"),
}

# SPDR → GICS L1 name (matches Sector.name convention)
SECTOR_ETF_MAP: dict[str, str] = {
    "XLC": "Communication Services", "XLE": "Energy",
    "XLF": "Financials", "XLI": "Industrials",
    "XLK": "Information Technology", "XLP": "Consumer Staples",
    "XLRE": "Real Estate", "XLU": "Utilities",
    "XLV": "Health Care", "XLY": "Consumer Discretionary",
    "XLB": "Materials",
}


async def ensure_market_context(db: AsyncSession) -> int:
    """Idempotently create the context instruments, wire sector
    ETFs to their GICS sectors, and place them in the universe
    hierarchy. Returns count ensured.

    Universe placement:
    - every context instrument → 'global' (known layer)
    - sector SPDRs → 'eligible' + 'approved' — they are tradeable
      market exposures, so the screener sees them. Fundamentals
      criteria will report insufficient_data (ETFs have no 10-K
      facts); technical criteria still score them honestly.
    """
    from app.models.universe import Universe
    from app.services.universe import set_membership

    sector_ids = dict(
        (await db.execute(select(Sector.name, Sector.id))).all())
    universes = dict(
        (await db.execute(select(Universe.name, Universe.id))).all())

    n = 0
    for sym, (name, cls, grp) in CONTEXT_INSTRUMENTS.items():
        inst, _ = await get_or_create(
            db, Instrument, {"symbol": sym},
            {"name": name, "asset_class": cls, "currency": "USD",
             "is_active": True, "listing_status": "active"})
        if inst is None:
            continue
        n += 1
        if grp == "sector" and inst.sector_id is None:
            inst.sector_id = sector_ids.get(SECTOR_ETF_MAP.get(sym))
        await get_or_create(
            db, InstrumentIdentifier,
            {"instrument_id": inst.id, "scheme": "ticker",
             "value": sym},
            {"is_primary": True})
        if "global" in universes:
            await set_membership(
                db, universe_name="global", instrument=inst,
                status="active", reason="market context")
        if grp == "sector":
            for uname in ("eligible", "approved"):
                if uname in universes:
                    await set_membership(
                        db, universe_name=uname, instrument=inst,
                        status="active",
                        reason="sector ETF — tradeable market context")
    return n


# share-count concepts (EDGAR aliases) — market_cap = shares × close
_SHARES_CONCEPTS = [
    "us-gaap:WeightedAverageNumberOfSharesOutstandingBasic",
    "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",
    "us-gaap:CommonStockSharesOutstanding",
]


async def update_adv(db: AsyncSession) -> int:
    """Refresh derived market stats per instrument:
      - avg_dollar_volume_30d = mean(close×volume) over the last 30
        daily bars — drives the liquidity dimension
      - market_cap = latest EDGAR share count × last close — feeds the
        universe eligibility gate (was never written → 'market_cap
        unknown' blocked every eligible-tier promotion)"""
    from app.services.fundamentals_query import latest_instant
    insts = (await db.execute(select(Instrument))).scalars().all()
    updated = 0
    for inst in insts:
        bars = (await db.execute(
            select(OhlcvBar.close, OhlcvBar.volume)
            .where(OhlcvBar.instrument_id == inst.id,
                   OhlcvBar.timeframe == "1d")
            .order_by(OhlcvBar.time.desc()).limit(30))).all()
        vals = [float(c) * float(v) for c, v in bars
                if c is not None and v]
        if vals:
            inst.avg_dollar_volume_30d = sum(vals) / len(vals)
            updated += 1
        if bars:
            shares = await latest_instant(
                db, inst.id, _SHARES_CONCEPTS, datetime.now(UTC))
            close = float(bars[0][0])
            if shares and close:
                inst.market_cap = shares * close
    return updated


async def refresh_quotes(db: AsyncSession,
                         symbols: list[str] | None = None) -> int:
    """Yahoo chart-meta → market_quotes (source='yahoo'). The chart
    endpoint's meta block carries regularMarketPrice, previousClose,
    dayHigh/Low etc. — no quote-API crumb needed."""
    ya = YahooAdapter()
    syms = symbols or list(CONTEXT_INSTRUMENTS)
    n = 0
    for sym in syms:
        try:
            q = await ya.fetch_quote(sym)
        except Exception:
            continue
        if q is None:
            continue
        inst = (await db.execute(
            select(Instrument).where(Instrument.symbol == sym))
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        row = (await db.execute(
            select(MarketQuote).where(
                MarketQuote.source == "yahoo",
                MarketQuote.symbol == sym))
        ).scalar_one_or_none()
        if row is None:
            row = MarketQuote(source="yahoo", symbol=sym, ts=now)
            db.add(row)
        row.instrument_id = inst.id if inst else None
        row.bid = q.get("bid")
        row.ask = q.get("ask")
        row.mid = q.get("mid")
        row.day_open = q.get("day_open")
        row.prev_close = q.get("prev_close")
        row.ts = now
        n += 1
    return n


async def refresh_alpaca_quotes(db: AsyncSession,
                                symbols: list[str] | None = None
                                ) -> int:
    """Alpaca snapshot → market_quotes (source='alpaca'). Snapshot is
    the richest single call: NBBO-ish bid/ask (IEX tape on the free
    tier), last trade, today's open and prior close in one response.
    Second real-time source alongside MT4/Tiingo/Yahoo."""
    from app.providers.alpaca import AlpacaAdapter
    from app.providers.base import ProviderConfigError
    try:
        ad = AlpacaAdapter()
    except ProviderConfigError:
        return 0
    syms = symbols or list(CONTEXT_INSTRUMENTS)
    n = 0
    for sym in syms:
        if sym.startswith("^"):
            continue  # IEX/SIP carry stocks/ETFs, not indices
        try:
            snap = await ad.snapshot(sym)
        except Exception:
            continue
        if not snap:
            continue
        q = snap.get("latestQuote") or {}
        t = snap.get("latestTrade") or {}
        day = snap.get("dailyBar") or {}
        prev = snap.get("prevDailyBar") or {}
        bid, ask = q.get("bp"), q.get("ap")
        mid = ((bid + ask) / 2 if bid is not None and ask is not None
               else t.get("p"))
        if mid is None:
            continue
        inst = (await db.execute(
            select(Instrument).where(Instrument.symbol == sym))
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        row = (await db.execute(
            select(MarketQuote).where(
                MarketQuote.source == "alpaca",
                MarketQuote.symbol == sym))
        ).scalar_one_or_none()
        if row is None:
            row = MarketQuote(source="alpaca", symbol=sym, ts=now)
            db.add(row)
        row.instrument_id = inst.id if inst else None
        row.bid = bid
        row.ask = ask
        row.mid = mid
        row.day_open = day.get("o")
        row.prev_close = prev.get("c")
        row.ts = now
        n += 1
    return n
