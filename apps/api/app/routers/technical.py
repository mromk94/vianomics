from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.session import get_db
from app.models.instruments import Instrument
from app.models.market import MarketQuote, OhlcvBar
from app.services import technical as ti
from app.services import technical_engine as te
from app.services import trading_days as td
from app.services.technical import Bar

router = APIRouter(prefix="/technical", tags=["technical"])


async def _inst(db, symbol):
    inst = (
        await db.execute(
            select(Instrument).where(Instrument.symbol == symbol.upper())
        )
    ).scalar_one_or_none()
    if inst is None:
        raise HTTPException(404, f"{symbol} not in security master")
    return inst


@router.get("/signal/{symbol}")
async def signal(symbol: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Both branches evaluated on completed bars."""
    inst = await _inst(db, symbol)
    return await te.evaluate(db, inst, utcnow())


@router.get("/bars/{symbol}")
async def bars(
    symbol: str,
    timeframe: str = Query("1d", pattern="^(1d|2d|3d|1w|1mo|1y)$"),
    limit: int = Query(400, le=2000),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """OHLCV for charting — aggregated w/ provisional flag.

    Self-healing staleness: if stored bars lag the last completed
    trading session (per the NYSE calendar — weekends/holidays are
    never expected bars), the symbol is re-ingested inline before
    serving. If the newest session's EOD bar still hasn't landed
    (market open / provider lag), a provisional bar is appended from
    the latest yahoo MarketQuote — flagged, never mistaken for a
    completed session."""
    inst = await _inst(db, symbol)

    async def _load() -> list:
        return (await db.execute(
            select(OhlcvBar)
            .where(OhlcvBar.instrument_id == inst.id,
                   OhlcvBar.timeframe == "1d")
            .order_by(OhlcvBar.time.desc())
            .limit(limit))).scalars().all()

    rows = await _load()
    now = utcnow()
    last_d = rows[0].time.date() if rows else None
    behind = (td.sessions_behind(last_d, now) if last_d else 999)

    refreshed = False
    if behind > 0:
        try:
            from datetime import timedelta
            from app.ingestion import jobs as ing
            from app.providers.yahoo import YahooAdapter
            # tail-only fetch — a self-heal must stay fast, not replay
            # the full 2000-onward history
            start = ((last_d - timedelta(days=10)).isoformat()
                     if last_d else
                     (now.date() - timedelta(days=400)).isoformat())
            await ing.ingest_stooq_bars(
                db, YahooAdapter(), inst.symbol, start=start)
            await db.commit()
            rows = await _load()
            last_d = rows[0].time.date() if rows else None
            behind = (td.sessions_behind(last_d, now)
                      if last_d else 999)
            refreshed = True
        except Exception:
            await db.rollback()   # serve what we have + stale flag

    daily = [Bar(b.time, float(b.open), float(b.high), float(b.low),
                 float(b.close), float(b.volume))
             for b in reversed(rows)]

    # provisional today-bar — a fresher quote than the last stored
    # bar means the session exists even if its EOD bar doesn't yet
    if daily and daily[-1].t.date() < now.date():
        q = (await db.execute(
            select(MarketQuote).where(
                MarketQuote.source == "yahoo",
                MarketQuote.symbol == inst.symbol))
        ).scalar_one_or_none()
        if q is not None and q.mid and q.ts.date() == now.date():
            o = float(q.day_open or q.prev_close or q.mid)
            c = float(q.mid)
            daily.append(Bar(now, o, max(o, c), min(o, c), c, 0.0,
                             provisional=True))

    agg = ti.aggregate(daily, timeframe)
    return {
        "symbol": inst.symbol, "timeframe": timeframe,
        "source": "yahoo", "delayed": True,
        "as_of": (agg[-1].t.date().isoformat() if agg else None),
        "latest_stored": last_d.isoformat() if last_d else None,
        "stale": behind > 0 and behind < 999,
        "sessions_behind": behind if behind < 999 else None,
        "refreshed": refreshed,
        "bars": [
            {"time": b.t.date().isoformat(), "open": b.o, "high": b.h,
             "low": b.l, "close": b.c, "volume": b.v,
             "provisional": b.provisional}
            for b in agg
        ],
    }


@router.get("/scan")
async def scan(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Quick decision scan across instruments with bars. Cached 5 min —
    signals only change when new daily bars land."""
    from app.services import cache
    hit = cache.get("technical:scan", 300)
    if hit is not None:
        return hit
    insts = (await db.execute(select(Instrument))).scalars().all()
    out = []
    for inst in insts:
        r = await te.evaluate(db, inst, utcnow())
        out.append({
            "symbol": inst.symbol,
            "decision": r["decision"],
            "mr": r.get("mean_reversion", {}).get("decision"),
            "tf": r.get("trend_following", {}).get("decision"),
            "rsi_1d": r.get("mean_reversion", {}).get(
                "indicators", {}).get("rsi_10_1d"),
            "aroon_up": (r.get("trend_following", {})
                         .get("indicators", {}).get("aroon_25_1d") or {}
                        ).get("up"),
            "last_close": r.get("last_close"),
            "bars": r["bars"], "fresh": r["data_fresh"],
        })
    cache.put("technical:scan", out)
    return out
