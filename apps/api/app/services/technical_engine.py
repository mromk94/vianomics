"""Part 10 — Signal engine (mean-reversion + trend-following).

PARAMS_VERSION must bump when thresholds change so persisted signals
stay reproducible. Signals are computed on completed bars only —
provisional multi-TF buckets are dropped; daily last bar is the
latest EOD (Yahoo delayed) and freshness is reported from the
bar timestamp.

Mean-reversion: trigger RSI(10)<30 (primary, per SOW); confirmations
reported separately — CMI<30 (chop), %R(13)<−80, %R(52)<−80,
bullish reversal pattern, volume confirm. Decision = ENTRY_SIGNAL
only when trigger + all required confirms (configurable via
`mr_require`).

Trend-following: trigger Aroon(25) up>99; confirmation sequence
MACD→ADX→Ichimoku→Breakout→Volume — each gate recorded; ENTRY_SIGNAL
when the full sequence passes.

Technical signals never assert fundamental quality.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.instruments import Instrument
from app.models.market import OhlcvBar
from app.services import technical as ti
from app.services.technical import Bar

PARAMS_VERSION = "technical/v1.0"

PARAMS = {
    "rsi_period": 10, "rsi_trigger": 30,
    "cmi_period": 21, "cmi_max": 30,
    "wr_fast": 13, "wr_slow": 52, "wr_trigger": -80,
    "aroon_period": 25, "aroon_trigger": 99,
    "adx_min": 25, "sr_lookback": 60, "breakout_lookback": 20,
    "vol_mult": 1.5,
    "mr_require": ["rsi", "cmi", "wr"],  # configurable confirmations
    "max_bar_age_days": 7,               # freshness gate
}


async def _bars(db, inst_id, as_of, limit=600) -> list[Bar]:
    rows = (
        await db.execute(
            select(OhlcvBar)
            .where(OhlcvBar.instrument_id == inst_id,
                   OhlcvBar.timeframe == "1d",
                   OhlcvBar.time <= as_of)
            .order_by(OhlcvBar.time.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [Bar(b.time, float(b.open), float(b.high), float(b.low),
                float(b.close), float(b.volume))
            for b in reversed(rows)]


def _completed(agg: list[Bar]) -> list[Bar]:
    return [b for b in agg if not b.provisional]


def evaluate_mean_reversion(daily: list[Bar]) -> dict:
    p = PARAMS
    weekly = _completed(ti.aggregate(daily, "1w"))
    monthly = _completed(ti.aggregate(daily, "1mo"))
    d2 = _completed(ti.aggregate(daily, "2d"))
    d3 = _completed(ti.aggregate(daily, "3d"))

    ind = {
        "rsi_10_1d": ti.rsi([b.c for b in daily], p["rsi_period"]),
        "rsi_10_2d": ti.rsi([b.c for b in d2], p["rsi_period"]),
        "rsi_10_3d": ti.rsi([b.c for b in d3], p["rsi_period"]),
        "rsi_10_1w": ti.rsi([b.c for b in weekly], p["rsi_period"]),
        "rsi_10_1mo": ti.rsi([b.c for b in monthly], p["rsi_period"]),
        "cmi_21": ti.cmi([b.c for b in daily], p["cmi_period"]),
        "wr_13": ti.williams_r(daily, p["wr_fast"]),
        "wr_52": ti.williams_r(daily, p["wr_slow"]),
    }
    sr = ti.support_resistance(daily, p["sr_lookback"])
    rev = ti.bullish_reversal(daily)
    volc = ti.volume_confirm(daily, mult=p["vol_mult"])

    rsi = ind["rsi_10_1d"]
    trigger = rsi is not None and rsi < p["rsi_trigger"]
    confirms = {
        "rsi": trigger,
        "cmi": ind["cmi_21"] is not None and ind["cmi_21"] < p["cmi_max"],
        "wr": (ind["wr_13"] is not None and ind["wr_13"] < p["wr_trigger"]) or
              (ind["wr_52"] is not None and ind["wr_52"] < p["wr_trigger"]),
        "reversal": rev["pattern"] is not None,
        "volume": bool(volc and volc["confirmed"]),
    }
    required = p["mr_require"]
    passed = all(confirms.get(k) for k in required)
    # higher-timeframe RSI is informational (long warm-up); only
    # daily-frame indicators gate validity
    missing = [k for k in ("rsi_10_1d", "cmi_21", "wr_13", "wr_52")
               if ind.get(k) is None]
    if missing:
        decision = "invalid_data"
    elif trigger and passed:
        decision = "entry_signal"
    else:
        decision = "wait"
    return {
        "branch": "mean_reversion",
        "indicators": ind,
        "confirmations": confirms,
        "trigger": {"rsi_10_1d": rsi, "threshold": p["rsi_trigger"],
                    "fired": trigger},
        "levels": sr, "reversal": rev, "volume": volc,
        "decision": decision,
        "explanation": _explain_mr(trigger, confirms, missing, ind),
    }


def _explain_mr(trigger, confirms, missing, ind):
    if missing:
        return f"insufficient history for: {', '.join(missing)}"
    if not trigger:
        return f"RSI(10) {ind['rsi_10_1d']:.0f} — above 30 trigger; wait"
    fails = [k for k, ok in confirms.items() if not ok]
    if fails:
        return (f"RSI(10) oversold but confirmations missing: "
                f"{', '.join(fails)}")
    return "RSI(10)<30 with CMI+%R alignment — mean-reversion entry"


def evaluate_trend_following(daily: list[Bar]) -> dict:
    p = PARAMS
    weekly = _completed(ti.aggregate(daily, "1w"))
    monthly = _completed(ti.aggregate(daily, "1mo"))
    closes = [b.c for b in daily]

    ar_d = ti.aroon(daily, p["aroon_period"])
    ar_w = ti.aroon(weekly, p["aroon_period"])
    ar_m = ti.aroon(monthly, p["aroon_period"])
    mc = ti.macd(closes)
    ax = ti.adx(daily)
    ichi = ti.ichimoku(daily)
    brk = ti.breakout(daily, p["breakout_lookback"])
    volc = ti.volume_confirm(daily, mult=p["vol_mult"])
    sr = ti.support_resistance(daily, p["sr_lookback"])

    gates = {
        "aroon": ar_d is not None and ar_d["up"] > p["aroon_trigger"],
        "macd": bool(mc and mc["bullish"]),
        "adx": ax is not None and ax > p["adx_min"],
        "ichimoku": bool(ichi and ichi["above_cloud"]),
        "breakout": bool(brk and brk["broken"]),
        "volume": bool(volc and volc["confirmed"]),
    }
    seq = ["aroon", "macd", "adx", "ichimoku", "breakout", "volume"]
    # sequence: stop at first failing gate
    first_fail = next((g for g in seq if not gates[g]), None)
    missing = []
    if ar_d is None or mc is None or ax is None or ichi is None:
        missing = [k for k, v in
                   {"aroon": ar_d, "macd": mc, "adx": ax,
                    "ichimoku": ichi}.items() if v is None]
    decision = ("invalid_data" if missing else
                "entry_signal" if first_fail is None else "wait")
    return {
        "branch": "trend_following",
        "indicators": {
            "aroon_25_1d": ar_d, "aroon_25_1w": ar_w, "aroon_25_1mo": ar_m,
            "macd": mc, "adx_14": ax, "ichimoku": ichi,
            "breakout_20": brk,
        },
        "sequence": seq,
        "confirmations": gates,
        "first_failed_gate": first_fail,
        "levels": sr, "volume": volc,
        "decision": decision,
        "explanation": _explain_tf(gates, first_fail, missing, ar_d),
    }


def _explain_tf(gates, first_fail, missing, ar_d):
    if missing:
        return f"insufficient history for: {', '.join(missing)}"
    if not gates["aroon"]:
        v = ar_d["up"] if ar_d else None
        return (f"Aroon(25) up {v:.0f} — below 99 trigger; wait"
                if v is not None else "no Aroon")
    if first_fail:
        return f"trend sequence halted at {first_fail} gate"
    return "full trend sequence confirmed — trend-following entry"


async def evaluate(
    db: AsyncSession, inst: Instrument, as_of: datetime
) -> dict:
    daily = await _bars(db, inst.id, as_of)
    last_bar = daily[-1].t if daily else None
    if last_bar and last_bar.tzinfo is None:
        last_bar = last_bar.replace(tzinfo=timezone.utc)
    age = (as_of - last_bar).days if last_bar else None
    fresh = age is not None and age <= PARAMS["max_bar_age_days"]

    base = {
        "symbol": inst.symbol,
        "as_of": as_of.isoformat(),
        "params_version": PARAMS_VERSION,
        "bars": len(daily),
        "last_close": float(daily[-1].c) if daily else None,
        "last_bar_time": last_bar.isoformat() if last_bar else None,
        "bar_age_days": age,
        "data_fresh": fresh,
        "data_source": "yahoo (delayed EOD)",
        "caveat": "Technical signal — not a judgment of business quality",
    }
    if len(daily) < 60:
        return {**base, "mean_reversion": {"decision": "invalid_data"},
                "trend_following": {"decision": "invalid_data"},
                "decision": "invalid_data",
                "explanation": f"only {len(daily)} bars (<60)"}
    mr = evaluate_mean_reversion(daily)
    tf = evaluate_trend_following(daily)
    if not fresh:
        mr["decision"] = "invalid_data" if mr["decision"] != "invalid_data" else mr["decision"]
        tf["decision"] = "invalid_data" if tf["decision"] != "invalid_data" else tf["decision"]
        base["freshness_note"] = (
            f"last bar {age}d old — signals provisional, not live")
    decision = ("entry_signal" if "entry_signal" in
                (mr["decision"], tf["decision"]) else
                "wait" if "wait" in (mr["decision"], tf["decision"])
                else "invalid_data")
    return {**base, "mean_reversion": mr, "trend_following": tf,
            "decision": decision,
            "explanation": f"MR={mr['decision']}; TF={tf['decision']}"}
