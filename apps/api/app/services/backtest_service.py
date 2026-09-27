"""Backtest service — load stored OHLCV, run engine, persist the
versioned run. Never fabricates history: missing bars are reported
as a coverage limitation."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.backtest import BacktestRun
from app.models.instruments import Instrument
from app.models.market import OhlcvBar
from app.services.backtest import (
    BTParams, ENGINE_VERSION, monte_carlo, run_backtest, walk_forward,
)
from app.services.technical import Bar


async def _bars_for(db: AsyncSession, inst: Instrument) -> list[Bar]:
    rows = (
        await db.execute(
            select(OhlcvBar)
            .where(OhlcvBar.instrument_id == inst.id,
                   OhlcvBar.timeframe == "1d")
            .order_by(OhlcvBar.time))
    ).scalars().all()
    return [Bar(t=r.time, o=float(r.open), h=float(r.high),
                l=float(r.low), c=float(r.close),
                v=float(r.volume or 0)) for r in rows]


async def run_and_persist(
    db: AsyncSession, symbols: list[str], params: BTParams,
    name: str | None = None, with_validation: bool = True,
) -> dict:
    instruments = (
        await db.execute(
            select(Instrument).where(Instrument.symbol.in_(
                [s.upper() for s in symbols])))
    ).scalars().all()
    bars_by_symbol: dict[str, list[Bar]] = {}
    coverage: dict[str, dict] = {}
    missing: list[str] = []
    for inst in instruments:
        bars = await _bars_for(db, inst)
        if len(bars) < 60:
            missing.append(inst.symbol)
            continue
        bars_by_symbol[inst.symbol] = bars
        coverage[inst.symbol] = {
            "bars": len(bars),
            "from": bars[0].t.isoformat()[:10],
            "to": bars[-1].t.isoformat()[:10]}

    not_found = [s for s in symbols
                 if s.upper() not in {i.symbol for i in instruments}]
    limitations = []
    if missing:
        limitations.append(f"insufficient bars (<60): {missing}")
    if not_found:
        limitations.append(f"not in security master: {not_found}")
    limitations.append("universe = current members only — "
                       "survivorship bias possible")
    limitations.append("fundamentals are point-in-time from EDGAR but "
                       "coverage starts at ingestion date — earlier "
                       "periods unavailable")

    result = run_backtest(bars_by_symbol, params)
    result["data_coverage"] = coverage
    result["limitations"] = limitations

    if with_validation:
        result["validation"] = {
            "walk_forward": walk_forward(bars_by_symbol, params),
            "monte_carlo": monte_carlo(result["trades"],
                                       capital=params.initial_capital),
        }
    else:
        result["validation"] = {}

    rec = BacktestRun(
        name=name or f"{params.strategy} on {len(bars_by_symbol)}syms",
        universe=sorted(bars_by_symbol),
        params=result["params"], engine_version=ENGINE_VERSION,
        data_snapshot=coverage, metrics=result["metrics"],
        equity_curve=result["equity_curve"], trades=result["trades"],
        validation=result.get("validation", {}))
    db.add(rec)
    await db.flush()
    result["run_id"] = rec.id
    return result
