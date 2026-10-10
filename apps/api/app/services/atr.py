"""ATR service — the docs' 'ATR Output' sheet, computed from stored
OHLCV bars.

Canonical formula (ATR Calculator.xls — verified cell-for-cell
against the workbook, AMD D sheet, 575/575 rows):

    per-bar ATR%  = ((H − L)ₜ + (H − L)ₜ₋₁) / 2 ÷ Openₜ₋₁
    Current ATR%  = mean of last W values   (W = 6d / 12w / 6mo —
                    the first horizon column of the averages row)
    Stop          = price × (1 − 1.5 × ATR%)  = price − 1.5 × ATR_abs
    Target        = price × (1 + 3 × ATR%)    = price + 3 × ATR_abs

where ATR_abs = close × ATR%. The pyramid state machine consumes
THIS number (ti.atr_workbook), so sheet and engine never diverge.
The sheet also reports average ATR% across fixed horizons — daily
(6/24/72/288/576 sessions), weekly (12/26/52/104/156 weeks), monthly
(6/12/24/36/60 months) — volatility-regime context.
"""

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.instruments import Instrument
from app.models.market import OhlcvBar

DAILY_WINDOWS = (6, 24, 72, 288, 576)      # ~1wk, 1mo, 3mo, 1y, 2y
WEEKLY_WINDOWS = (12, 26, 52, 104, 156)    # 3mo, 6mo, 1y, 2y, 3y
MONTHLY_WINDOWS = (6, 12, 24, 36, 60)      # 6mo, 1y, 2y, 3y, 5y


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


def _resample_sessions(bars: list[dict], n: int) -> list[dict]:
    """Daily bars → n-SESSION buckets (2-day/3-day frames). Buckets
    count trading sessions, not calendar days, and are anchored to
    the newest bar — every bucket is exactly n consecutive sessions
    except possibly the oldest, which keeps the history remainder.
    Same convention as technical.aggregate's 2d/3d path."""
    out = []
    i = len(bars)
    while i > 0:
        chunk = bars[max(0, i - n):i]
        i -= n
        out.append({
            "time": chunk[0]["time"], "open": chunk[0]["open"],
            "high": max(x["high"] for x in chunk),
            "low": min(x["low"] for x in chunk),
            "close": chunk[-1]["close"],
        })
    out.reverse()
    return out


def _frame_report(bars: list[dict], window: int) -> dict:
    """Workbook column for one timeframe: per-bar range%
    = mean(H−L of 2 bars) ÷ prev OPEN; 'current ATR%' = the mean of
    the last `window` of those; atr_abs = close × atr_pct. `window`
    is the frame's first horizon column (6d/12w/6m); the UI may pass
    a wider one to inspect longer-horizon current ATR."""
    atr_series = []
    for i in range(1, len(bars)):
        b, p = bars[i], bars[i - 1]
        if p["open"]:
            atr_series.append(
                ((b["high"] - b["low"]) + (p["high"] - p["low"])) / 2
                / p["open"])
    closes = [b["close"] for b in bars]
    last_c = closes[-1] if closes else None
    w = min(window, len(atr_series))
    atr_pct = (sum(atr_series[-w:]) / w) if w else None
    return {
        "bars": len(bars), "last_close": last_c,
        "atr_abs": (last_c * atr_pct
                    if atr_pct is not None and last_c else None),
        "atr_pct": atr_pct,
        "atr_pct_series": atr_series,
        "tr_pcts": atr_series,   # vol-regime reads the same series
        "period": w,
        "last_time": bars[-1]["time"].isoformat() if bars else None,
    }


async def atr_report(db: AsyncSession, symbol: str,
                     d: int = 6, w: int = 12, m: int = 6) -> dict | None:
    """Full ATR Output sheet for a symbol — daily/weekly/monthly ATR,
    each frame's 'current ATR' averaged over its workbook window
    (6 sessions / 12 weeks / 6 months); the UI may pass wider windows
    to inspect longer-horizon current ATR. Returns None when the
    instrument isn't in the security master; reports honest
    insufficiency when bar history is short."""
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

    need = d + 2   # window of range% values, each needing a prior bar
    if len(bars) < need:
        return {
            "symbol": inst.symbol,
            "as_of": datetime.now(UTC).isoformat(),
            "bars": len(bars), "insufficient": True,
            "note": f"needs ≥{need} daily bars, has {len(bars)}",
        }

    daily = _frame_report(bars, d)
    # n-session frames keep the SAME session horizon as the daily
    # window: the workbook's '6' means 6 trading sessions, so a 2d
    # frame averages 3 buckets, a 3d frame 2 — not 6 buckets (12/18
    # sessions), which silently doubled-tripled the lookback.
    two_d = _frame_report(_resample_sessions(bars, 2), max(2, d // 2))
    three_d = _frame_report(_resample_sessions(bars, 3), max(2, d // 3))
    weekly = _frame_report(_resample(bars, "1w"), w)
    monthly = _frame_report(_resample(bars, "1mo"), m)

    def _sub(fr: dict, n: int) -> dict:
        # horizon windows in bucket units — scaled so they span the
        # same session horizons as the daily frame (6d/24d/72d…)
        return {
            "atr_abs": fr["atr_abs"], "atr_pct": fr["atr_pct"],
            "period": max(2, d // n), "bars": fr["bars"],
            "unit_sessions": n,
            "window_sessions": max(2, d // n) * n,
            "windows": _window_avgs(
                fr["atr_pct_series"],
                tuple(max(1, x // n) for x in DAILY_WINDOWS)),
        }

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
            # multi-day frames from the same price history the bar
            # endpoint serves — 2-session and 3-session buckets run
            # through the identical workbook formula
            "sub": {"2d": _sub(two_d, 2), "3d": _sub(three_d, 3)},
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
