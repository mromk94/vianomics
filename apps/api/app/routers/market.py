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
    from app.services import cache
    hit = cache.get("market:snapshot", 300)
    if hit is not None:
        return hit
    insts = (await db.execute(select(Instrument))).scalars().all()
    # one query — last 2 bars per instrument
    all_bars = (
        await db.execute(
            select(OhlcvBar)
            .where(OhlcvBar.timeframe == "1d")
            .order_by(OhlcvBar.time.desc()))
    ).scalars().all()
    by_inst: dict[str, list] = {}
    for b in all_bars:
        lst = by_inst.setdefault(b.instrument_id, [])
        if len(lst) < 2:
            lst.append(b)
    out = []
    for inst in insts:
        bars = by_inst.get(inst.id) or []
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
    out = sorted(out, key=lambda x: x["symbol"])
    cache.put("market:snapshot", out)
    return out
