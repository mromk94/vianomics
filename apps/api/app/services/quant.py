"""Part 8 — Quantitative engine (pure functions).

Conventions (documented, configurable):
- returns: simple (period) by default; `log=True` for log returns
- annualization: ×√periods (vol), (1+r)^n−1 (returns); `periods_per_year`
  must be passed (252 daily, 52 weekly, 12 monthly)
- sharpe/sortino: rf is annualized, converted per-period
- missing data: pairwise-skip — returns list stays length-aligned,
  None entries dropped before stats; `n` reported so callers can
  enforce minimums (never silently imputed)
- beta/correlation: sample covariance (n−1)
"""

from math import sqrt
from statistics import fmean
from typing import Iterable


def returns(prices: list[float], log: bool = False) -> list[float]:
    """Period returns from a price series (oldest→newest)."""
    out = []
    for i in range(1, len(prices)):
        a, b = prices[i - 1], prices[i]
        if a and b and a > 0:
            from math import log as ln
            out.append(ln(b / a) if log else b / a - 1)
    return out


def ann_return(rets: list[float], periods_per_year: int = 252) -> float | None:
    if not rets:
        return None
    total = 1.0
    for r in rets:
        total *= 1 + r
    n = len(rets)
    return total ** (periods_per_year / n) - 1


def volatility(rets: list[float], periods_per_year: int = 252) -> float | None:
    """Annualized stdev (sample, n−1)."""
    if len(rets) < 2:
        return None
    mu = fmean(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    return sqrt(var) * sqrt(periods_per_year)


def downside_vol(rets: list[float], mar: float = 0.0,
                 periods_per_year: int = 252) -> float | None:
    """Downside deviation below MAR (annualized)."""
    downs = [min(0.0, r - mar) for r in rets]
    if not downs:
        return None
    var = sum(d * d for d in downs) / len(downs)
    return sqrt(var) * sqrt(periods_per_year)


def sharpe(rets: list[float], rf_annual: float = 0.04,
           periods_per_year: int = 252) -> float | None:
    """(ann_return − rf) / ann_vol."""
    ar, vol = ann_return(rets, periods_per_year), volatility(rets, periods_per_year)
    if ar is None or vol is None or vol == 0:
        return None
    return (ar - rf_annual) / vol


def sortino(rets: list[float], rf_annual: float = 0.04,
            periods_per_year: int = 252) -> float | None:
    ar = ann_return(rets, periods_per_year)
    dv = downside_vol(rets, periods_per_year=periods_per_year)
    if ar is None or dv is None or dv == 0:
        return None
    return (ar - rf_annual) / dv


def max_drawdown(prices: list[float]) -> dict | None:
    """Peak-to-trough % and indices."""
    if len(prices) < 2:
        return None
    peak, peak_i = prices[0], 0
    worst, w_pair = 0.0, (0, 0)
    for i, p in enumerate(prices):
        if p > peak:
            peak, peak_i = p, i
        dd = (p - peak) / peak
        if dd < worst:
            worst, w_pair = dd, (peak_i, i)
    return {"max_drawdown": worst, "from_idx": w_pair[0], "to_idx": w_pair[1]}


def _paired(a: Iterable, b: Iterable) -> tuple[list, list]:
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    return ([p[0] for p in pairs], [p[1] for p in pairs])


def correlation(rets_a: list, rets_b: list) -> dict | None:
    """Pearson on pairwise-complete period returns."""
    a, b = _paired(rets_a, rets_b)
    if len(a) < 3:
        return None
    ma, mb = fmean(a), fmean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (len(a) - 1)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((y - mb) ** 2 for y in b) / (len(b) - 1)
    if va <= 0 or vb <= 0:
        return None
    return {"rho": cov / sqrt(va * vb), "n": len(a)}


def beta(asset_rets: list, market_rets: list) -> dict | None:
    """β = cov(asset, market)/var(market), pairwise-complete."""
    a, m = _paired(asset_rets, market_rets)
    if len(a) < 3:
        return None
    ma, mm = fmean(a), fmean(m)
    cov = sum((x - ma) * (y - mm) for x, y in zip(a, m)) / (len(a) - 1)
    var = sum((y - mm) ** 2 for y in m) / (len(m) - 1)
    if var <= 0:
        return None
    return {"beta": cov / var, "n": len(a)}


def momentum_12_1(prices: list[float], trading_days: int = 21) -> float | None:
    """12-minus-1-month momentum on daily prices."""
    if len(prices) < 253:
        return None
    past = prices[-253 + trading_days]  # t−12m+1m
    now = prices[-trading_days - 1]     # t−1m
    if past <= 0:
        return None
    return now / past - 1


def sma(prices: list[float], window: int) -> float | None:
    if len(prices) < window:
        return None
    return fmean(prices[-window:])


def pct_above_sma(closes_map: dict[str, list[float]], window: int = 200) -> float | None:
    """Breadth: share of series trading above their SMA."""
    flags = []
    for s in closes_map.values():
        m = sma(s, window)
        if m is not None:
            flags.append(s[-1] > m)
    if not flags:
        return None
    return sum(flags) / len(flags)


# ── factor scoring ──

def fcf_yield(fcf, market_cap) -> float | None:
    if fcf is None or market_cap is None or market_cap <= 0:
        return None
    return float(fcf) / float(market_cap)


def factor_scores(inputs: dict, weights: dict | None = None) -> dict:
    """Composite factor score 0–100 from sub-scores (each 0–100 or
    None). Missing sub-scores are excluded AND disclosed — the
    composite is scored on available factors only, with coverage
    reported."""
    w = weights or {"momentum": 0.2, "value": 0.2, "quality": 0.25,
                    "growth": 0.2, "low_vol": 0.15}
    sub = {
        "momentum": inputs.get("momentum"),
        "value": inputs.get("value"),
        "quality": inputs.get("quality"),
        "growth": inputs.get("growth"),
        "low_vol": inputs.get("low_vol"),
    }
    avail = {k: v for k, v in sub.items() if v is not None}
    if not avail:
        return {"composite": None, "subscores": sub,
                "coverage": 0, "weights_used": {}}
    tw = sum(w.get(k, 0) for k in avail)
    comp = sum(avail[k] * w.get(k, 0) for k in avail) / tw if tw else None
    return {
        "composite": comp,
        "subscores": sub,
        "coverage": len(avail) / len(sub),
        "weights_used": {k: w.get(k, 0) / tw for k in avail} if tw else {},
    }


def portfolio_exposure(positions: list[dict]) -> dict:
    """positions: [{weight, subscores:{factor:score}}] → weighted avg."""
    factors: dict[str, list] = {}
    for p in positions:
        for k, v in (p.get("subscores") or {}).items():
            if v is not None:
                factors.setdefault(k, []).append((p["weight"], v))
    out = {}
    for k, pairs in factors.items():
        tw = sum(w for w, _ in pairs)
        out[k] = sum(w * v for w, v in pairs) / tw if tw else None
    return out


def event_study(
    prices: list[float], bench_prices: list[float],
    event_indices: list[int], window: int = 5,
) -> dict:
    """Abnormal returns around event days: AR_t = r_stock − r_bench,
    averaged across events. Returns mean AR path −w..+w."""
    sr, br = returns(prices), returns(bench_prices)
    res = []
    for e in event_indices:
        path = []
        for off in range(-window, window + 1):
            i = e + off - 1  # returns index (shifted by 1 vs prices)
            if 0 <= i < len(sr) and i < len(br):
                path.append(sr[i] - br[i])
            else:
                path.append(None)
        res.append(path)
    n = len(res)
    if not n:
        return {"events": 0, "mean_ar": None}
    mean_ar = []
    for j in range(2 * window + 1):
        col = [r[j] for r in res if r[j] is not None]
        mean_ar.append(fmean(col) if col else None)
    return {
        "events": n,
        "window": window,
        "mean_ar": mean_ar,
        "cum_mean_ar": sum(x for x in mean_ar if x is not None),
    }
