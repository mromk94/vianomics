from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.instruments import Instrument
from app.models.market import MarketQuote, OhlcvBar

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


@router.get("/quotes")
async def quotes(db: AsyncSession = Depends(get_db)):
    """Latest quotes across all sources — the live tape. MT4 rows are
    pushed by the EA every minute; yahoo rows refresh on backfill.
    `age_s` lets the UI show freshness instead of implying live."""
    from app.services import cache
    hit = cache.get("market:quotes", 30)
    if hit is not None:
        return hit
    rows = (await db.execute(
        select(MarketQuote))).scalars().all()
    names = dict((await db.execute(
        select(Instrument.symbol, Instrument.name))).all())
    now = datetime.now(UTC)
    out = []
    for q in rows:
        ts = q.ts if q.ts.tzinfo else q.ts.replace(tzinfo=UTC)
        out.append({
            "symbol": q.symbol,
            "name": names.get(q.symbol),
            "source": q.source,
            "bid": float(q.bid) if q.bid is not None else None,
            "ask": float(q.ask) if q.ask is not None else None,
            "mid": float(q.mid) if q.mid is not None else None,
            "spread": (float(q.ask - q.bid)
                       if q.ask is not None and q.bid is not None
                       else None),
            "prev_close": (float(q.prev_close)
                           if q.prev_close is not None else None),
            "ts": ts.isoformat(),
            "age_s": int((now - ts).total_seconds()),
            "stale": (now - ts).total_seconds() > 3600,
        })
    out.sort(key=lambda x: (x["source"], x["symbol"]))
    cache.put("market:quotes", out)
    return out


@router.get("/signals")
async def signals(db: AsyncSession = Depends(get_db)):
    """Full signal surface per instrument — everything the connected
    providers support: trend (SMA50/200 distance, ADX), momentum
    (RSI-14, MACD histogram), volatility (ATR%), 52-week position,
    returns (1D/1W/1M), volume ratio. Computed from stored daily bars;
    refreshes whenever ingestion lands new bars."""
    from app.services import cache
    from app.services import technical as ti
    hit = cache.get("market:signals", 300)
    if hit is not None:
        return hit
    insts = (await db.execute(
        select(Instrument).where(Instrument.is_active))
    ).scalars().all()
    out = []
    for inst in insts:
        bars = (await db.execute(
            select(OhlcvBar)
            .where(OhlcvBar.instrument_id == inst.id,
                   OhlcvBar.timeframe == "1d")
            .order_by(OhlcvBar.time.desc()).limit(320))).scalars().all()
        if len(bars) < 30:
            continue
        bars = list(reversed(bars))
        t_bars = [ti.Bar(t=b.time, o=float(b.open), h=float(b.high),
                         l=float(b.low), c=float(b.close),
                         v=float(b.volume or 0))
                  for b in bars]
        closes = [b.c for b in t_bars]
        last = closes[-1]
        sma50 = ti.sma(closes, 50)
        sma200 = ti.sma(closes, 200)
        atr_v = ti.atr(t_bars, 14)
        hi52 = max(closes[-250:]) if len(closes) >= 30 else None
        lo52 = min(closes[-250:]) if closes else None
        vols = [b.v for b in t_bars[-20:] if b.v]
        out.append({
            "symbol": inst.symbol, "name": inst.name,
            "asset_class": inst.asset_class,
            "close": last,
            "as_of": bars[-1].time.isoformat()[:10],
            "ret_1d": (last / closes[-2] - 1) if len(closes) > 1
            else None,
            "ret_1w": (last / closes[-6] - 1) if len(closes) > 5
            else None,
            "ret_1m": (last / closes[-22] - 1) if len(closes) > 21
            else None,
            "sma50": sma50, "sma200": sma200,
            "above_sma50": last > sma50 if sma50 else None,
            "above_sma200": last > sma200 if sma200 else None,
            "rsi14": ti.rsi(closes, 14),
            "adx14": ti.adx(t_bars, 14),
            "macd": ti.macd(closes),
            "atr_pct": (atr_v / last) if atr_v and last else None,
            "from_52w_high": (last / hi52 - 1) if hi52 else None,
            "from_52w_low": (last / lo52 - 1) if lo52 else None,
            "vol_ratio": (t_bars[-1].v / (sum(vols) / len(vols)))
            if vols and t_bars[-1].v else None,
        })
    out.sort(key=lambda x: x["symbol"])
    cache.put("market:signals", out)
    return out
