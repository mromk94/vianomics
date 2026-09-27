from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.session import get_db
from app.models.instruments import Instrument
from app.models.market import OhlcvBar
from app.services import technical as ti
from app.services import technical_engine as te
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
    timeframe: str = Query("1d", pattern="^(1d|2d|3d|1w|1mo)$"),
    limit: int = Query(400, le=2000),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """OHLCV for charting — aggregated w/ provisional flag."""
    inst = await _inst(db, symbol)
    rows = (
        await db.execute(
            select(OhlcvBar)
            .where(OhlcvBar.instrument_id == inst.id,
                   OhlcvBar.timeframe == "1d")
            .order_by(OhlcvBar.time.desc())
            .limit(limit)
        )
    ).scalars().all()
    daily = [Bar(b.time, float(b.open), float(b.high), float(b.low),
                 float(b.close), float(b.volume))
             for b in reversed(rows)]
    agg = ti.aggregate(daily, timeframe)
    return {
        "symbol": inst.symbol, "timeframe": timeframe,
        "source": "yahoo", "delayed": True,
        "bars": [
            {"time": b.t.date().isoformat(), "open": b.o, "high": b.h,
             "low": b.l, "close": b.c, "volume": b.v,
             "provisional": b.provisional}
            for b in agg
        ],
    }


@router.get("/scan")
async def scan(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Quick decision scan across instruments with bars."""
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
    return out
