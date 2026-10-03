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
from app.models.instruments import Instrument, InstrumentIdentifier
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
    """Idempotently create the context instruments. Returns count
    ensured."""
    n = 0
    for sym, (name, cls, _grp) in CONTEXT_INSTRUMENTS.items():
        inst, _ = await get_or_create(
            db, Instrument, {"symbol": sym},
            {"name": name, "asset_class": cls, "currency": "USD",
             "is_active": True, "listing_status": "active"})
        if inst is None:
            continue
        n += 1
        await get_or_create(
            db, InstrumentIdentifier,
            {"instrument_id": inst.id, "scheme": "ticker",
             "value": sym},
            {"is_primary": True})
    return n


async def update_adv(db: AsyncSession) -> int:
    """Refresh avg_dollar_volume_30d = mean(close×volume) over the
    instrument's last 30 daily bars. Drives the liquidity dimension."""
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
