"""Part 9/11 — Macro regime + market regime + sector rotation +
Fear&Greed/VIX overlays.

Transparent, versioned rule engine (RULES_VERSION). Each run records
the feature values + rule hits — classifications are reproducible,
never authoritative when inputs are stale (stale inputs listed and
downgrade confidence via `insufficient` verdicts).

PIT rule: features use only observations whose published_at ≤ as_of.
FRED fredgraph rows carry published_at=observed_at (latest vintage) —
the honest limitation is recorded in `stale_inputs`/`notes`.

Threshold inclusivity (policy-documented):
- F&G bands: [0,25) accum | [25,45) cautious | [45,65) neutral |
  [65,80) reduce | [80,100] aggressive — lower bound inclusive.
- VIX: <15 normal | [15,20) tighten | [20,30) reduce size | ≥30 risk-off
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.macro import RegimeRun
from app.models.market import MacroObservation, MacroSeries, OhlcvBar
from app.models.instruments import Instrument
from app.models.universe import UniverseMembership
from app.services.universe import universe_by_name

RULES_VERSION = "regime-rules/v1.0"

# sector rotation map (SOW Part 11) — inputs to portfolio analysis,
# not forecasts
SECTOR_ROTATION = {
    "recovery": ["Information Technology", "Consumer Discretionary",
                 "Industrials"],
    "expansion": ["Financials", "Materials", "Energy"],
    "slowdown": ["Energy", "Materials", "Industrials"],
    "recession": ["Consumer Staples", "Utilities", "Health Care"],
}

FRED_SERIES = {
    "GDP": ("GDP", "growth"),
    "UNRATE": ("Unemployment rate", "employment"),
    "PAYEMS": ("Nonfarm payrolls", "employment"),
    "CPIAUCSL": ("CPI", "inflation"),
    "PPIACO": ("PPI", "inflation"),
    "INDPRO": ("Industrial production", "growth"),
    "UMCSENT": ("Consumer sentiment", "confidence"),
    "T10Y2Y": ("Yield curve 10y−2y", "rates"),
    "FEDFUNDS": ("Fed funds", "rates"),
    "BAMLH0A0HYM2": ("HY credit spread", "credit"),
    "DTWEXBGS": ("Dollar index", "fx"),
    "DCOILWTICO": ("WTI crude", "commodities"),
    "WALCL": ("Fed balance sheet", "liquidity"),
    "VIXCLS": ("VIX", "volatility"),
}

# staleness judged against period-start observed_at — monthly series
# get ~2.5 cycles, quarterly GDP ~1.5, daily ~1 week
STALE_AFTER_DAYS = {
    "UNRATE": 75, "PAYEMS": 75, "CPIAUCSL": 75, "PPIACO": 75,
    "INDPRO": 75, "UMCSENT": 75, "T10Y2Y": 10, "FEDFUNDS": 75,
    "BAMLH0A0HYM2": 10, "DTWEXBGS": 10, "DCOILWTICO": 10,
    "WALCL": 20, "VIXCLS": 7, "GDP": 200,
}


async def _latest_obs(
    db: AsyncSession, code: str, as_of: datetime
) -> tuple[float | None, datetime | None]:
    """Latest observation whose publication ≤ as_of (PIT)."""
    series = (
        await db.execute(
            select(MacroSeries).where(
                MacroSeries.source == "fred", MacroSeries.code == code
            )
        )
    ).scalar_one_or_none()
    if series is None:
        return None, None
    obs = (
        await db.execute(
            select(MacroObservation)
            .where(
                MacroObservation.series_id == series.id,
                MacroObservation.observed_at <= as_of,
            )
            .order_by(MacroObservation.observed_at.desc())
        )
    ).scalars().first()
    if obs is None:
        return None, None
    return float(obs.value), obs.observed_at


async def _series_window(
    db: AsyncSession, code: str, as_of: datetime, n: int = 4
) -> list[tuple[datetime, float]]:
    series = (
        await db.execute(
            select(MacroSeries).where(
                MacroSeries.source == "fred", MacroSeries.code == code
            )
        )
    ).scalar_one_or_none()
    if series is None:
        return []
    rows = (
        await db.execute(
            select(MacroObservation)
            .where(
                MacroObservation.series_id == series.id,
                MacroObservation.observed_at <= as_of,
            )
            .order_by(MacroObservation.observed_at.desc())
            .limit(n)
        )
    ).scalars().all()
    return [(o.observed_at, float(o.value)) for o in reversed(rows)]


def _change(vals: list[tuple[datetime, float]], periods: int = 2) -> float | None:
    if len(vals) < periods + 1:
        return None
    return vals[-1][1] - vals[-1 - periods][1]


async def _index_close(db: AsyncSession, symbol: str,
                       as_of: datetime, limit: int = 1) -> list[float]:
    """Latest daily closes for a context symbol (e.g. ^VIX) — the
    Yahoo-bars fallback when a FRED series is absent/stale. PIT:
    only bars with time ≤ as_of."""
    inst = (
        await db.execute(
            select(Instrument).where(Instrument.symbol == symbol)
        )
    ).scalar_one_or_none()
    if inst is None:
        return []
    rows = (
        await db.execute(
            select(OhlcvBar.close)
            .where(OhlcvBar.instrument_id == inst.id,
                   OhlcvBar.timeframe == "1d", OhlcvBar.time <= as_of)
            .order_by(OhlcvBar.time.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [float(c) for c in reversed(rows)]


async def _spy_bars(db: AsyncSession, as_of: datetime, limit=260) -> list[float]:
    inst = (
        await db.execute(
            select(Instrument).where(Instrument.symbol == "SPY")
        )
    ).scalar_one_or_none()
    if inst is None:
        return []
    rows = (
        await db.execute(
            select(OhlcvBar)
            .where(OhlcvBar.instrument_id == inst.id,
                   OhlcvBar.timeframe == "1d", OhlcvBar.time <= as_of)
            .order_by(OhlcvBar.time.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [float(b.close) for b in reversed(rows)]


async def _breadth(db: AsyncSession, as_of: datetime) -> float | None:
    """% of approved-universe tickers above their own SMA50."""
    u = await universe_by_name(db, "approved")
    if u is None:
        return None
    ids = (
        await db.execute(
            select(UniverseMembership.instrument_id).where(
                UniverseMembership.universe_id == u.id,
                UniverseMembership.status == "active",
            )
        )
    ).scalars().all()
    flags = []
    for iid in ids:
        rows = (
            await db.execute(
                select(OhlcvBar.close)
                .where(OhlcvBar.instrument_id == iid,
                       OhlcvBar.timeframe == "1d",
                       OhlcvBar.time <= as_of)
                .order_by(OhlcvBar.time.desc())
                .limit(50)
            )
        ).scalars().all()
        if len(rows) >= 50:
            flags.append(rows[0] > sum(float(r) for r in rows) / 50)
    return sum(flags) / len(flags) if flags else None


def vix_band(vix: float | None) -> str | None:
    """Policy bands: <15 normal | 15–20 tighten | 20–30 reduce | ≥30 off."""
    if vix is None:
        return None
    if vix < 15:
        return "normal"
    if vix < 20:
        return "tighten_risk"
    if vix < 30:
        return "reduce_position_size"
    return "risk_off"


def fear_greed_overlay(score: float | None) -> str | None:
    """[0,25) accum | [25,45) cautious | [45,65) neutral |
    [65,80) reduce | [80,100] aggressive."""
    if score is None:
        return None
    if score < 25:
        return "accumulation"
    if score < 45:
        return "cautious_accumulation"
    if score < 65:
        return "neutral"
    if score < 80:
        return "reduce"
    return "aggressive_risk_reduction"


async def classify(db: AsyncSession, as_of: datetime) -> dict:
    """Full regime classification — feature set + rule hits recorded."""
    feats: dict[str, Any] = {}
    stale: list[str] = []
    hits: list[str] = []

    async def feat(code: str, n: int = 2):
        v, at = await _latest_obs(db, code, as_of)
        if at is not None and at.tzinfo is None:
            at = at.replace(tzinfo=UTC)  # SQLite stores naive
        win = await _series_window(db, code, as_of, n=n + 1)
        stale_days = STALE_AFTER_DAYS.get(code, 45)
        is_stale = at is None or (as_of - at) > timedelta(days=stale_days)
        if is_stale:
            stale.append(code)
        feats[code] = {
            "value": v, "observed_at": at.isoformat() if at else None,
            "stale": is_stale,
            "window": [w[1] for w in win],
        }
        return v, win

    # ── features ──
    hy, hy_w = await feat("BAMLH0A0HYM2", 4)
    yc, _ = await feat("T10Y2Y")
    vix, _ = await feat("VIXCLS")
    if vix is None:
        # FRED VIXCLS absent/stale → ^VIX daily bar (yahoo) — same
        # underlying series, different transport. Recorded in feats.
        vx = await _index_close(db, "^VIX", as_of)
        if vx:
            vix = vx[-1]
            feats["VIXCLS"].update({
                "value": vix, "fallback": "^VIX:yahoo",
                "stale": False})
            if "VIXCLS" in stale:
                stale.remove("VIXCLS")
    un, un_w = await feat("UNRATE", 4)
    indpro, ip_w = await feat("INDPRO", 6)
    cpi, cpi_w = await feat("CPIAUCSL", 12)
    ff, _ = await feat("FEDFUNDS")
    await feat("GDP")  # reference
    await feat("PAYEMS")
    await feat("PPIACO")
    await feat("UMCSENT")
    await feat("DTWEXBGS")
    await feat("DCOILWTICO")
    await feat("WALCL")

    spy = await _spy_bars(db, as_of)
    breadth = await _breadth(db, as_of)
    feats["SPY_trend"] = {
        "value": spy[-1] if spy else None,
        "sma200": (sum(spy[-200:]) / 200) if len(spy) >= 200 else None,
        "above": (spy[-1] > sum(spy[-200:]) / 200) if len(spy) >= 200 else None,
        "stale": len(spy) < 50,
    }
    if len(spy) < 50:
        stale.append("SPY")
    feats["breadth_50"] = {"value": breadth,
                           "stale": breadth is None}
    if breadth is None:
        stale.append("breadth")

    # ── economic regime rules ──
    un_3m = _change(un_w, 3)          # Δ over ~3 obs
    ip_6m = _change(ip_w, 6)          # Δ over ~6 obs
    hy_chg = _change(hy_w, 2)

    econ = "expansion"
    if (hy is not None and hy > 6) or \
       (un_3m is not None and un_3m > 0.5) or \
       (yc is not None and yc < 0 and ip_6m is not None and ip_6m < 0):
        econ = "recession"
        if hy is not None and hy > 6:
            hits.append("recession:hy_spread>6")
        if un_3m is not None and un_3m > 0.5:
            hits.append("recession:unrate_+0.5pp")
        if yc is not None and yc < 0:
            hits.append("recession:curve_inverted+ip_neg")
    elif (hy_chg is not None and hy_chg < -0.5 and
          un is not None and un > 6):
        econ = "recovery"
        hits.append("recovery:spread_falling_from_high_un")
    elif (ip_6m is not None and ip_6m < 0) or \
         (hy_chg is not None and hy_chg > 0.75):
        econ = "slowdown"
        hits.append("slowdown:ip_neg|spread_widening")
    else:
        hits.append("expansion:default(growth_ok+spreads_ok)")

    # critical inputs missing → not verified
    if "INDPRO" in stale and "UNRATE" in stale and "BAMLH0A0HYM2" in stale:
        econ = "insufficient_data"
        hits.append("insufficient:core macro inputs stale/missing")

    # ── market regime rules ──
    spy_above = feats["SPY_trend"]["above"]
    market = "neutral"
    if (vix is not None and vix >= 30) or \
       (spy_above is False and hy is not None and hy > 5):
        market = "risk_off"
        hits.append("risk_off:vix≥30|below_sma200+hy>5")
    elif vix is not None and vix < 20 and spy_above and \
            hy is not None and hy < 4:
        market = "risk_on"
        hits.append("risk_on:vix<20+above_sma200+hy<4")
    else:
        hits.append("neutral:mixed signals")

    # ── fear & greed proxy composite (documented) ──
    # components (each 0-100): vix_inverse, spy_trend, breadth, hy_inverse
    comps = {}
    if vix is not None:
        comps["vix_inverse"] = max(0.0, min(100.0, (40 - vix) / 40 * 100))
    if spy_above is not None and len(spy) >= 200:
        sma = sum(spy[-200:]) / 200
        comps["spy_vs_sma200"] = max(0.0, min(
            100.0, 50 + (spy[-1] / sma - 1) * 500))
    if breadth is not None:
        comps["breadth"] = breadth * 100
    if hy is not None:
        comps["hy_inverse"] = max(0.0, min(100.0, (10 - hy) / 10 * 100))
    fg = round(sum(comps.values()) / len(comps)) if comps else None

    return {
        "as_of": as_of.isoformat(),
        "rules_version": RULES_VERSION,
        "econ_regime": econ,
        "market_regime": market,
        "fear_greed": fg,
        "fg_components": comps,
        "overlay": fear_greed_overlay(fg),
        "vix": vix,
        "vix_band": vix_band(vix),
        "features": feats,
        "rule_hits": hits,
        "stale_inputs": stale,
        "sector_preferences": {
            s: "favored" for s in SECTOR_ROTATION.get(econ, [])
        },
        "sector_rotation_map": SECTOR_ROTATION,
        "notes": [
            "fredgraph values are latest-vintage; no ALFRED vintages without API key",
            "regime classification is an input to portfolio analysis, not a forecast",
            "F&G is a transparent proxy composite (vix/spy/breadth/hy), not CNN index",
        ],
    }


async def run_and_persist(db: AsyncSession, as_of: datetime) -> RegimeRun:
    c = await classify(db, as_of)
    r = RegimeRun(
        as_of=as_of,
        rules_version=RULES_VERSION,
        econ_regime=c["econ_regime"],
        market_regime=c["market_regime"],
        fear_greed=c["fear_greed"],
        overlay=c["overlay"],
        vix=c["vix"],
        vix_band=c["vix_band"],
        features=c["features"],
        rule_hits=c["rule_hits"],
        stale_inputs=c["stale_inputs"],
        sector_preferences=c["sector_preferences"],
    )
    db.add(r)
    return r
