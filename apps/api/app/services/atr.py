"""ATR service — the docs' 'ATR Output' sheet, computed from stored
OHLCV bars.

Canonical formulas (new docs — 'ATR Pyramid Engine' python spec +
'ATR Calculator.xls'):

    TR          = max(H − L, |H − prevC|, |L − prevC|)
    ATR%        = SMA14(TR) / Close          ← drives the state machine
    Stop        = price × (1 − 1.5 × ATR%)   = price − 1.5 × ATR_abs
    Target      = price × (1 + 3 × ATR%)     = price + 3 × ATR_abs

The Excel sheet also reports average ATR% across fixed horizons —
daily (6/24/72/288/576 sessions), weekly (12/26/52/104/156 weeks),
monthly (6/12/24/36/60 months). Those are volatility regime context:
short-horizon ATR% vs long-horizon ATR% shows expansion/contraction.
"""

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.instruments import Instrument
from app.models.market import OhlcvBar

ATR_PERIOD = 14
DAILY_WINDOWS = (6, 24, 72, 288, 576)      # ~1wk, 1mo, 3mo, 1y, 2y
WEEKLY_WINDOWS = (12, 26, 52, 104, 156)    # 3mo, 6mo, 1y, 2y, 3y
MONTHLY_WINDOWS = (6, 12, 24, 36, 60)      # 6mo, 1y, 2y, 3y, 5y


def _tr_series(bars: list[dict]) -> list[float]:
    """True Range per bar — needs the previous close, so the first
    bar uses its own H−L."""
    out = []
    prev_c = None
    for b in bars:
        h, l, c = b["high"], b["low"], b["close"]
        if prev_c is None:
            out.append(h - l)
        else:
            out.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
        prev_c = c
    return out


def _sma(vals: list[float], n: int) -> float | None:
    return sum(vals[-n:]) / n if len(vals) >= n else None


def _window_avgs(tr_pcts: list[float], windows: tuple[int, ...]) -> dict:
    """Mean ATR% over each horizon (Excel's 'Average Daily ATR' row)."""
    return {str(w): sum(tr_pcts[-w:]) / w
            for w in windows if len(tr_pcts) >= w}


def _resample(bars: list[dict], period: str) -> list[dict]:
    """Daily bars → weekly (ISO) or monthly OHLC groups."""
    groups: dict = defaultdict(list)
    for b in bars:
        d = b["time"]
        key = (d.isocalendar()[:2] if period == "1w"
               else (d.year, d.month))
        groups[key].append(b)
    out = []
    for key in sorted(groups):
        g = groups[key]
        out.append({
            "time": g[-1]["time"], "open": g[0]["open"],
            "high": max(x["high"] for x in g),
            "low": min(x["low"] for x in g),
            "close": g[-1]["close"],
        })
    return out


def _frame_report(bars: list[dict], period: int = ATR_PERIOD) -> dict:
    """ATR metrics for one timeframe's bar set at a given period —
    the workbook's ATR column is a rolling SMA_n(TR); horizon windows
    are averages OF that ATR% series (not of raw TR)."""
    trs = _tr_series(bars)
    closes = [b["close"] for b in bars]
    tr_pcts = [t / c for t, c in zip(trs, closes) if c]
    # per-bar ATR% series — mean of trailing `period` TRs / that bar's close
    atr_pcts = [sum(trs[i - period + 1:i + 1]) / period / closes[i]
                for i in range(period - 1, len(bars)) if closes[i]]
    atr_abs = _sma(trs, period)
    last_c = closes[-1] if closes else None
    atr_pct = (atr_abs / last_c
               if atr_abs is not None and last_c else None)
    return {
        "bars": len(bars), "last_close": last_c,
        "atr_abs": atr_abs,
        "atr_pct": atr_pct,
        "atr_pct_series": atr_pcts,
        "tr_pcts": tr_pcts,
        "period": period,
        "last_time": bars[-1]["time"].isoformat() if bars else None,
    }


async def atr_report(db: AsyncSession, symbol: str,
                     d: int = ATR_PERIOD, w: int = ATR_PERIOD,
                     m: int = ATR_PERIOD) -> dict | None:
    """Full ATR Output sheet for a symbol — daily/weekly/monthly ATR
    each at its own editable period (the UI exposes 3d/8d/2w/5m…).
    Returns None when the instrument isn't in the security master;
    reports honest insufficiency when bar history is short."""
    d = max(1, min(d, 200)); w = max(1, min(w, 200))
    m = max(1, min(m, 60))
    inst = (await db.execute(
        select(Instrument).where(Instrument.symbol == symbol.upper()))
    ).scalar_one_or_none()
    if inst is None:
        return None
    rows = (await db.execute(
        select(OhlcvBar.time, OhlcvBar.open, OhlcvBar.high,
               OhlcvBar.low, OhlcvBar.close)
        .where(OhlcvBar.instrument_id == inst.id,
               OhlcvBar.timeframe == "1d")
        .order_by(OhlcvBar.time))).all()
    bars = [{"time": r[0], "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4])} for r in rows]

    need = max(d, 2) + 1
    if len(bars) < need:
        return {
            "symbol": inst.symbol,
            "as_of": datetime.now(UTC).isoformat(),
            "bars": len(bars), "insufficient": True,
            "note": f"needs ≥{need} daily bars, has {len(bars)}",
        }

    daily = _frame_report(bars, d)
    weekly = _frame_report(_resample(bars, "1w"), w)
    monthly = _frame_report(_resample(bars, "1mo"), m)

    return {
        "symbol": inst.symbol, "name": inst.name,
        "as_of": daily["last_time"],
        "close": daily["last_close"],
        "daily": {
            "atr_abs": daily["atr_abs"],
            "atr_pct": daily["atr_pct"],
            "period": d,
            "windows": _window_avgs(daily["atr_pct_series"], DAILY_WINDOWS),
            "bars": daily["bars"],
        },
        "weekly": {
            "atr_abs": weekly["atr_abs"],
            "atr_pct": weekly["atr_pct"],
            "period": w,
            "windows": _window_avgs(weekly["atr_pct_series"], WEEKLY_WINDOWS),
            "bars": weekly["bars"],
        },
        "monthly": {
            "atr_abs": monthly["atr_abs"],
            "atr_pct": monthly["atr_pct"],
            "period": m,
            "windows": _window_avgs(monthly["atr_pct_series"], MONTHLY_WINDOWS),
            "bars": monthly["bars"],
        },
        "periods": {"daily": d, "weekly": w, "monthly": m},
        # engine contract — the pyramid state machine consumes these.
        # Levels are emitted PER TIMEFRAME so the sheet matches the
        # configured sleeve_atr_timeframe (1d|1w|1mo), not always 1d.
        "pyramid": {
            "stop_mult": 1.5, "target_mult": 3.0,
            # daily levels kept for backward compatibility
            "stop": (daily["last_close"] - 1.5 * daily["atr_abs"]
                     if daily["atr_abs"] else None),
            "target": (daily["last_close"] + 3.0 * daily["atr_abs"]
                       if daily["atr_abs"] else None),
            "levels": {
                tf: {
                    "stop": (fr["last_close"] - 1.5 * fr["atr_abs"]
                             if fr["atr_abs"] and fr["last_close"]
                             else None),
                    "target": (fr["last_close"] + 3.0 * fr["atr_abs"]
                               if fr["atr_abs"] and fr["last_close"]
                               else None),
                    "atr_abs": fr["atr_abs"],
                    "atr_pct": fr["atr_pct"],
                }
                for tf, fr in (("1d", daily), ("1w", weekly),
                               ("1mo", monthly))
            },
            "vol_regime": (
                "expanding" if daily["atr_abs"] and
                daily["tr_pcts"] and len(daily["tr_pcts"]) >= 24 and
                sum(daily["tr_pcts"][-6:]) / 6 >
                sum(daily["tr_pcts"][-24:]) / 24 * 1.15
                else "contracting" if daily["tr_pcts"] and
                len(daily["tr_pcts"]) >= 24 and
                sum(daily["tr_pcts"][-6:]) / 6 <
                sum(daily["tr_pcts"][-24:]) / 24 * 0.85
                else "normal"),
        },
    }
