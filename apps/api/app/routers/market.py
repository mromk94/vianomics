from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.instruments import Instrument
from app.models.market import OhlcvBar

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/snapshot")
async def snapshot(db: AsyncSession = Depends(get_db)):
    """Last two closes per instrument → price + day change + range."""
    insts = (await db.execute(select(Instrument))).scalars().all()
    out = []
    for inst in insts:
        bars = (
            await db.execute(
                select(OhlcvBar)
                .where(OhlcvBar.instrument_id == inst.id,
                       OhlcvBar.timeframe == "1d")
                .order_by(OhlcvBar.time.desc()).limit(2))
        ).scalars().all()
        if not bars:
            continue
        last, prev = bars[0], bars[1] if len(bars) > 1 else None
        chg = (float(last.close) / float(prev.close) - 1
               if prev else None)
        out.append({
            "symbol": inst.symbol, "name": inst.name,
            "asset_class": inst.asset_class,
            "close": float(last.close),
            "change_pct": round(chg * 100, 2) if chg else None,
            "high": float(last.high), "low": float(last.low),
            "volume": float(last.volume or 0),
            "as_of": last.time.isoformat()[:10],
        })
    return sorted(out, key=lambda x: x["symbol"])
