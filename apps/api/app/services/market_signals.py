"""Per-instrument signal surface — shared by /market/signals and the
symbol detail endpoint so both show identical numbers."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.instruments import Instrument
from app.models.market import OhlcvBar
from app.services import technical as ti


async def signal_row(db: AsyncSession, inst: Instrument) -> dict | None:
    """Full technical surface for one instrument: trend (SMA50/200,
    ADX), momentum (RSI-14, MACD), volatility (ATR%), 52-week position,
    returns (1D/1W/1M), volume ratio. None under 30 bars."""
    bars = (await db.execute(
        select(OhlcvBar)
        .where(OhlcvBar.instrument_id == inst.id,
               OhlcvBar.timeframe == "1d")
        .order_by(OhlcvBar.time.desc()).limit(320))).scalars().all()
    if len(bars) < 30:
        return None
    bars = list(reversed(bars))
    t_bars = [ti.Bar(t=b.time, o=float(b.open), h=float(b.high),
                     l=float(b.low), c=float(b.close),
                     v=float(b.volume or 0))
              for b in bars]
    closes = [b.c for b in t_bars]
    last = closes[-1]
    sma50 = ti.sma(closes, 50)
    sma200 = ti.sma(closes, 200)
    atr_v = (ti.atr_workbook(t_bars, "1d") or {}).get("atr_abs")
    hi52 = max(closes[-250:]) if len(closes) >= 30 else None
    lo52 = min(closes[-250:]) if closes else None
    hi20 = max(b.h for b in t_bars[-20:]) if len(t_bars) >= 20 else None
    lo20 = min(b.l for b in t_bars[-20:]) if len(t_bars) >= 20 else None
    vols = [b.v for b in t_bars[-20:] if b.v]
    adxv = ti.adx(t_bars, 14)
    cmiv = ti.cmi(closes, 21)
    # regime read per the doc's Step-8 list: CMI>61.8 choppy,
    # ADX≥25 trending — both are honest "insufficient" when missing
    regime = ("choppy" if cmiv is not None and cmiv > 61.8
              else "trending" if adxv is not None and adxv >= 25
              else "balanced" if (cmiv is not None or adxv is not None)
              else None)
    return {
        "symbol": inst.symbol, "name": inst.name,
        "asset_class": inst.asset_class,
        "close": last,
        "as_of": bars[-1].time.isoformat()[:10],
        "ret_1d": (last / closes[-2] - 1) if len(closes) > 1 else None,
        "ret_1w": (last / closes[-6] - 1) if len(closes) > 5 else None,
        "ret_1m": (last / closes[-22] - 1) if len(closes) > 21 else None,
        "sma50": sma50, "sma200": sma200,
        "above_sma50": last > sma50 if sma50 else None,
        "above_sma200": last > sma200 if sma200 else None,
        "rsi14": ti.rsi(closes, 14),
        "williams_r": ti.williams_r(t_bars, 13),
        "adx14": adxv,
        "cmi": cmiv,
        "regime": regime,
        "macd": ti.macd(closes),
        "atr_abs": atr_v,
        "atr_pct": (atr_v / last) if atr_v and last else None,
        "from_52w_high": (last / hi52 - 1) if hi52 else None,
        "from_52w_low": (last / lo52 - 1) if lo52 else None,
        "hi_20": hi20, "lo_20": lo20,
        "vol_ratio": (t_bars[-1].v / (sum(vols) / len(vols)))
        if vols and t_bars[-1].v else None,
    }
