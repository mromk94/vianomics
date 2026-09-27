"""Quant service — per-instrument metrics from OHLCV + fundamentals."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.instruments import Instrument
from app.models.market import OhlcvBar
from app.services import quant as q
from app.services.fundamentals_query import fy_series, last_two, latest_instant
from app.services.green_zone import (
    C_CAPEXC, C_DEBT, C_EBIT, C_EQUITY, C_NI, C_OCF, C_REV, C_SHARES,
    C_TAX, C_PRETAX, C_CASH,
)
from app.services import finmetrics as fm
from app.db.base import utcnow

PPY = 252
RF = 0.04  # document: assumed risk-free, configurable


async def _closes(db, instrument_id, as_of, limit=400) -> list[float]:
    rows = (
        await db.execute(
            select(OhlcvBar)
            .where(OhlcvBar.instrument_id == instrument_id,
                   OhlcvBar.timeframe == "1d",
                   OhlcvBar.time <= as_of)
            .order_by(OhlcvBar.time.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [float(b.close) for b in reversed(rows)]


async def instrument_metrics(
    db: AsyncSession, inst: Instrument, as_of: datetime | None = None
) -> dict:
    as_of = as_of or utcnow()
    closes = await _closes(db, inst.id, as_of)

    # benchmark = SPY
    spy = (
        await db.execute(
            select(Instrument).where(Instrument.symbol == "SPY")
        )
    ).scalar_one_or_none()
    bench = await _closes(db, spy.id, as_of) if spy else []

    rets = q.returns(closes)
    brets = q.returns(bench)

    latest_px = closes[-1] if closes else None

    # fundamentals-derived factors
    rev = await fy_series(db, inst.id, C_REV, as_of)
    ni = await fy_series(db, inst.id, C_NI, as_of)
    ocf = await fy_series(db, inst.id, C_OCF, as_of)
    capex = await fy_series(db, inst.id, C_CAPEXC, as_of)
    ebit = await fy_series(db, inst.id, C_EBIT, as_of)
    shares_s = await fy_series(db, inst.id, C_SHARES, as_of)
    equity = await latest_instant(db, inst.id, C_EQUITY, as_of)
    debt = await latest_instant(db, inst.id, C_DEBT, as_of)
    cash = await latest_instant(db, inst.id, C_CASH, as_of)
    tax = await fy_series(db, inst.id, C_TAX, as_of)
    pretax = await fy_series(db, inst.id, C_PRETAX, as_of)

    sh = last_two(shares_s)[1]
    mcap = (latest_px * sh) if (latest_px and sh) else None
    fcf = fm.fcf(last_two(ocf)[1], last_two(capex)[1])
    roic = fm.roic(last_two(ebit)[1], last_two(tax)[1],
                   last_two(pretax)[1], debt, equity, cash)

    def cagr(s):
        items = sorted(s.items())
        if len(items) < 2 or items[0][1] <= 0:
            return None
        n = len(items) - 1
        return (items[-1][1] / items[0][1]) ** (1 / n) - 1

    mom = q.momentum_12_1(closes)
    vol = q.volatility(rets)
    fcfy = q.fcf_yield(fcf, mcap)
    rev_g = cagr(rev)

    def clamp01(x, lo, hi):
        if x is None:
            return None
        return max(0.0, min(100.0, (x - lo) / (hi - lo) * 100))

    subscores = {
        "momentum": clamp01(mom, -0.3, 0.5),
        "value": (100 - clamp01(1 / fcfy if fcfy and fcfy > 0 else None,
                                0, 60)) if fcfy else None,
        "quality": clamp01(float(roic) if roic else None, 0, 0.4),
        "growth": clamp01(rev_g, -0.1, 0.5),
        "low_vol": (100 - clamp01(vol, 0.1, 0.6)) if vol else None,
    }

    return {
        "symbol": inst.symbol,
        "as_of": as_of.isoformat(),
        "conventions": {
            "returns": "simple", "periods_per_year": PPY,
            "risk_free": RF, "benchmark": "SPY (stooq, delayed EOD)",
            "missing": "pairwise-skip, n reported",
        },
        "bars": len(closes),
        "latest_close": latest_px,
        "metrics": {
            "ann_return": q.ann_return(rets),
            "volatility": vol,
            "sharpe": q.sharpe(rets, RF),
            "sortino": q.sortino(rets, RF),
            "max_drawdown": q.max_drawdown(closes),
            "beta": q.beta(rets, brets),
            "correlation_spy": q.correlation(rets, brets),
            "momentum_12_1": mom,
            "fcf_yield": fcfy,
            "roic": float(roic) if roic else None,
            "rev_cagr": rev_g,
            "market_cap": mcap,
        },
        "factor_scores": q.factor_scores(subscores),
    }


async def correlation_matrix(
    db: AsyncSession, symbols: list[str], as_of: datetime | None = None
) -> dict:
    as_of = as_of or utcnow()
    series: dict[str, list[float]] = {}
    for s in symbols:
        inst = (
            await db.execute(
                select(Instrument).where(Instrument.symbol == s.upper())
            )
        ).scalar_one_or_none()
        if inst is None:
            continue
        closes = await _closes(db, inst.id, as_of, limit=300)
        series[s.upper()] = q.returns(closes)

    keys = list(series)
    matrix = []
    for a in keys:
        row = []
        for b in keys:
            c = q.correlation(series[a], series[b])
            row.append(None if c is None else c["rho"])
        matrix.append(row)
    return {"symbols": keys, "matrix": matrix,
            "conventions": {"returns": "daily simple", "n_max": 300}}
