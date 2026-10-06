"""Part 10 — Technical indicator library + multi-timeframe aggregation.

Conventions (documented):
- bars: (time, open, high, low, close, volume) lists oldest→newest;
  OHLCV floats; missing bars are simply absent (no imputation) —
  window indicators require len ≥ warm_up else None
- RSI: Wilder smoothing, period 10 by default
- CMI(21): |C_t − C_{t−21}| / Σ|ΔC| × 100 — low = choppy (mean-
  reversion friendly), high = trending
- Williams %R(n): −100 × (HH_n − C) / (HH_n − LL_n); <−80 oversold
- Aroon(25): up/down = 100 × (n − periods_since_high/low) / n
- ADX(14): Wilder DX smoothed
- MACD: EMA12−EMA26, signal EMA9
- Ichimoku: tenkan 9, kijun 26, senkou B 52, displacement 26
- ATR(14): Wilder
- Aggregation: ISO weeks / calendar months; the *last* bucket is
  marked provisional until the period closes — provisional bars are
  excluded from signal computation and reported separately
- Timezone: all bar times UTC
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass
class Bar:
    t: datetime
    o: float
    h: float
    l: float
    c: float
    v: float
    provisional: bool = False


# ── primitives ──

def _ema(vals: list[float], period: int) -> list[float]:
    k = 2 / (period + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def sma(vals: list[float], n: int) -> float | None:
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder RSI — needs period+1 closes."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def cmi(closes: list[float], period: int = 21) -> float | None:
    """Chande Momentum Oscillator — |net change| / Σ|changes| × 100."""
    if len(closes) < period + 1:
        return None
    recent = closes[-period - 1:]
    net = abs(recent[-1] - recent[0])
    total = sum(abs(recent[i] - recent[i - 1]) for i in range(1, len(recent)))
    if total == 0:
        return 0.0
    return net / total * 100


def williams_r(bars: list[Bar], period: int = 13) -> float | None:
    """−100×(HH−C)/(HH−LL) ∈ [−100, 0]; <−80 oversold."""
    if len(bars) < period:
        return None
    w = bars[-period:]
    hh = max(b.h for b in w)
    ll = min(b.l for b in w)
    if hh == ll:
        return -50.0
    return -100 * (hh - bars[-1].c) / (hh - ll)


def aroon(bars: list[Bar], period: int = 25) -> dict | None:
    """up/down ∈ [0,100]; up>99 = fresh high at bar 0."""
    if len(bars) < period + 1:
        return None
    w = bars[-period - 1:]
    hi_i = max(range(len(w)), key=lambda i: w[i].h)
    lo_i = min(range(len(w)), key=lambda i: w[i].l)
    up = 100 * (period - (len(w) - 1 - hi_i)) / period
    down = 100 * (period - (len(w) - 1 - lo_i)) / period
    return {"up": up, "down": down, "diff": up - down}


def adx(bars: list[Bar], period: int = 14) -> float | None:
    """Wilder ADX — needs ~2×period bars to stabilize."""
    if len(bars) < period * 2 + 2:
        return None
    trs, pdm, ndm = [], [], []
    for i in range(1, len(bars)):
        b, p = bars[i], bars[i - 1]
        tr = max(b.h - b.l, abs(b.h - p.c), abs(b.l - p.c))
        up, dn = b.h - p.h, p.l - b.l
        trs.append(tr)
        pdm.append(up if up > dn and up > 0 else 0.0)
        ndm.append(dn if dn > up and dn > 0 else 0.0)
    # Wilder-smooth
    tr = sum(trs[:period])
    pm, nm = sum(pdm[:period]), sum(ndm[:period])
    dxs = []
    for i in range(period, len(trs)):
        tr = tr - tr / period + trs[i]
        pm = pm - pm / period + pdm[i]
        nm = nm - nm / period + ndm[i]
        if tr == 0:
            continue
        pdi, ndi = 100 * pm / tr, 100 * nm / tr
        denom = pdi + ndi
        dxs.append(100 * abs(pdi - ndi) / denom if denom else 0)
    if len(dxs) < period:
        return None
    adxv = sum(dxs[:period]) / period
    for d in dxs[period:]:
        adxv = (adxv * (period - 1) + d) / period
    return adxv


def macd(closes: list[float], fast=12, slow=26, signal=9) -> dict | None:
    """EMA12−EMA26, signal=EMA9 of MACD; bullish cross detection."""
    if len(closes) < slow + signal:
        return None
    ef, es = _ema(closes, fast), _ema(closes, slow)
    line = [a - b for a, b in zip(ef, es)]
    sig = _ema(line[-(len(line)):], signal)
    hist = line[-1] - sig[-1]
    prev_hist = line[-2] - sig[-2] if len(line) > 1 else None
    return {
        "macd": line[-1], "signal": sig[-1], "hist": hist,
        "bullish": line[-1] > sig[-1],
        "crossed_up": prev_hist is not None and prev_hist <= 0 < hist,
    }


def ichimoku(bars: list[Bar]) -> dict | None:
    """tenkan9/kijun26/senkouB52, displacement 26 (cloud values from
    t−26 vs current price)."""
    if len(bars) < 78:
        return None

    def hh_ll(n, offset=0):
        w = bars[-n - offset:len(bars) - offset or None]
        return (max(b.h for b in w) + min(b.l for b in w)) / 2

    tenkan = hh_ll(9)
    kijun = hh_ll(26)
    senkou_a = (hh_ll(9, 26) + hh_ll(26, 26)) / 2  # displaced cloud top
    senkou_b = hh_ll(52, 26)
    price = bars[-1].c
    top, bot = max(senkou_a, senkou_b), min(senkou_a, senkou_b)
    return {
        "tenkan": tenkan, "kijun": kijun,
        "senkou_a": senkou_a, "senkou_b": senkou_b,
        "above_cloud": price > top, "below_cloud": price < bot,
        "in_cloud": bot <= price <= top,
    }


def atr(bars: list[Bar], period: int = 14) -> float | None:
    """Wilder ATR."""
    if len(bars) < period + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        b, p = bars[i], bars[i - 1]
        trs.append(max(b.h - b.l, abs(b.h - p.c), abs(b.l - p.c)))
    a = sum(trs[:period]) / period
    for tr in trs[period:]:
        a = (a * (period - 1) + tr) / period
    return a


def atr_sma(bars: list[Bar], period: int = 14) -> float | None:
    """Canonical VAIIP ATR — SMA_n(TR), the ATR Output sheet's
    formula ('ATR Calculator.xls' spec). The pyramid state machine
    consumes THIS number so stops always match the published sheet;
    `atr()` (Wilder) remains for indicator displays only."""
    if len(bars) < period + 1:
        return None
    trs = [max(b.h - b.l, abs(b.h - bars[i - 1].c),
               abs(b.l - bars[i - 1].c))
           for i, b in enumerate(bars) if i > 0]
    return sum(trs[-period:]) / period


# ── levels & patterns ──

def support_resistance(bars: list[Bar], lookback: int = 60) -> dict | None:
    """Recent swing extremes + current position within the range."""
    if len(bars) < lookback:
        return None
    w = bars[-lookback:]
    res = max(b.h for b in w)
    sup = min(b.l for b in w)
    c = bars[-1].c
    return {"resistance": res, "support": sup,
            "pct_from_resistance": (c - res) / res if res else None,
            "pct_from_support": (c - sup) / sup if sup else None}


def bullish_reversal(bars: list[Bar]) -> dict:
    """Simple candlestick reversal flags on last completed bar."""
    if len(bars) < 3:
        return {"pattern": None}
    p, c = bars[-2], bars[-1]
    body = abs(c.c - c.o)
    rng = c.h - c.l
    hammer = (rng > 0 and (min(c.o, c.c) - c.l) > 2 * body and
              (c.h - max(c.o, c.c)) < body and c.c > c.o)
    engulf = (p.c < p.o and c.c > c.o and c.c > p.o and c.o < p.c)
    out = {"hammer": hammer, "bullish_engulfing": engulf}
    out["pattern"] = ("hammer" if hammer else
                      "bullish_engulfing" if engulf else None)
    return out


def volume_confirm(bars: list[Bar], period: int = 20,
                   mult: float = 1.5) -> dict | None:
    """Last bar volume > mult × SMA(volume)."""
    if len(bars) < period + 1:
        return None
    avg = sum(b.v for b in bars[-period - 1:-1]) / period
    v = bars[-1].v
    return {"volume": v, "avg": avg,
            "ratio": v / avg if avg else None,
            "confirmed": avg > 0 and v > mult * avg}


def breakout(bars: list[Bar], lookback: int = 20) -> dict | None:
    """Close > prior N-day high."""
    if len(bars) < lookback + 1:
        return None
    prior_high = max(b.h for b in bars[-lookback - 1:-1])
    c = bars[-1].c
    return {"level": prior_high, "close": c, "broken": c > prior_high}


# ── multi-timeframe aggregation ──

def aggregate(bars: list[Bar], timeframe: str) -> list[Bar]:
    """Daily → 2d/3d/weekly/monthly. Last bucket flagged provisional
    unless its period has ended.

    2d/3d bucket TRADING SESSIONS, not calendar days — weekends and
    holidays hold no bars, so a 2d bar is "the last two sessions"
    (Fri+Mon), never a Friday+Sunday pair. Buckets are anchored to the
    newest bar: every returned row is exactly n consecutive sessions
    except possibly the oldest, which is a history remainder."""
    if timeframe == "1d":
        return bars
    if timeframe in ("2d", "3d"):
        n = int(timeframe[0])
        out = []
        i = len(bars)
        while i > 0:
            chunk = bars[max(0, i - n):i]
            i -= n
            out.append(Bar(chunk[-1].t, chunk[0].o,
                           max(x.h for x in chunk),
                           min(x.l for x in chunk), chunk[-1].c,
                           sum(x.v for x in chunk)))
        out.reverse()
        # newest bucket is complete by construction; only fewer than n
        # total bars makes it partial
        if out and len(bars) < n:
            out[-1].provisional = True
        return out
    out: list[Bar] = []
    cur: Bar | None = None
    cur_key = None

    def bucket_key(d: date):
        if timeframe == "1w":
            iso = d.isocalendar()
            return (iso[0], iso[1])
        if timeframe == "1mo":
            return (d.year, d.month)
        if timeframe == "1y":
            return d.year
        raise ValueError(f"unknown timeframe {timeframe}")

    for b in bars:
        k = bucket_key(b.t.date())
        if k != cur_key:
            if cur:
                out.append(cur)
            cur = Bar(b.t, b.o, b.h, b.l, b.c, b.v)
            cur_key = k
        else:
            cur.h = max(cur.h, b.h)
            cur.l = min(cur.l, b.l)
            cur.c = b.c
            cur.v += b.v
            cur.t = b.t
    if cur:
        out.append(cur)

    # mark last bucket provisional if period incomplete
    if out and timeframe in ("1w", "1mo"):
        last = out[-1]
        today = bars[-1].t.date() if bars else date.today()
        if timeframe == "1w":
            end = last.t.date() + timedelta(days=(6 - last.t.weekday()))
            last.provisional = today < end
        else:
            nxt = (last.t.date().replace(day=28) + timedelta(days=7))
            last.provisional = today < nxt.replace(day=1)
    return out
