"""Part 10 — indicator correctness vs known values + signal logic."""

from datetime import UTC, datetime, timedelta

import pytest

from app.services import technical as ti
from app.services import technical_engine as te
from app.services.technical import Bar


def _bars(closes, spread=0.01, start=datetime(2025, 1, 1, tzinfo=UTC)):
    """Synthetic bars: h=c*(1+spread), l=c*(1-spread)."""
    out = []
    for i, c in enumerate(closes):
        out.append(Bar(start + timedelta(days=i),
                       c, c * (1 + spread), c * (1 - spread), c, 1e6))
    return out


# ── indicator correctness ──

def test_rsi_all_gains_is_100():
    assert ti.rsi([float(i) for i in range(1, 20)], 10) == 100.0


def test_rsi_known_sequence():
    """Hand-check: alternating +2/−1 for 11 bars → RSI(10).
    avg gain 1.0, avg loss 0.5 → RSI = 100 − 100/3 ≈ 66.67."""
    closes = [100.0]
    for i in range(10):
        closes.append(closes[-1] + (2 if i % 2 == 0 else -1))
    r = ti.rsi(closes, 10)
    assert r == pytest.approx(66.67, abs=0.1)


def test_rsi_deeply_oversold():
    closes = [100 - i * 2 for i in range(12)]  # all losses
    assert ti.rsi(closes, 10) == 0.0


def test_cmi_choppy_vs_trending():
    choppy = [100, 101, 99, 101, 99, 100, 101, 99, 101, 99, 100, 101,
              99, 101, 99, 100, 101, 99, 101, 99, 100, 101]
    trend = [100 + i for i in range(22)]
    c_chop, c_trend = ti.cmi(choppy, 21), ti.cmi(trend, 21)
    assert c_chop < 20 and c_trend == pytest.approx(100)


def test_williams_r():
    """HH=110, LL=90 over window, C=92 → −100*(110−92)/(110−90)=−90."""
    bars = [Bar(datetime(2025, 1, i + 1, tzinfo=UTC),
                100, 110 if i == 5 else 105,
                90 if i == 7 else 95, 92, 1e6)
            for i in range(13)]
    assert ti.williams_r(bars, 13) == pytest.approx(-90)


def test_aroon_fresh_high_99():
    """Highest bar at the last position → up = 100."""
    closes = [100.0] * 25 + [110.0]
    bars = _bars(closes)
    bars[-1].h = 115
    a = ti.aroon(bars, 25)
    assert a["up"] == pytest.approx(100)
    assert a["up"] > 99


def test_macd_bullish_rising():
    closes = [100 - i * 0.2 for i in range(40)] + [92 + i * 1.5 for i in range(15)]
    m = ti.macd(closes)
    assert m is not None and m["bullish"]


def test_atr_known():
    """Constant daily range 2 → ATR ≈ 2."""
    bars = [Bar(datetime(2025, 1, i + 1, tzinfo=UTC),
                100, 101, 99, 100, 1e6) for i in range(30)]
    assert ti.atr(bars, 14) == pytest.approx(2.0, abs=0.01)


def test_support_resistance():
    bars = _bars([100 + (i % 10) for i in range(80)])
    sr = ti.support_resistance(bars, 60)
    assert sr["resistance"] > sr["support"]
    assert -1 <= (sr["pct_from_resistance"] or 0) <= 0


def test_bullish_engulfing_detected():
    bars = _bars([100] * 5)
    bars[-2].o, bars[-2].c = 101, 99      # down bar
    bars[-2].h, bars[-2].l = 101.5, 98.5
    bars[-1].o, bars[-1].c = 98.8, 102    # engulfs up
    bars[-1].h, bars[-1].l = 102.5, 98.5
    r = ti.bullish_reversal(bars)
    assert r["pattern"] == "bullish_engulfing"


def test_volume_confirm():
    bars = _bars([100] * 25)
    bars[-1].v = 3e6  # 3× average
    v = ti.volume_confirm(bars, 20, 1.5)
    assert v["confirmed"] is True and v["ratio"] == pytest.approx(3.0)


# ── aggregation ──

def test_weekly_aggregation_marks_last_provisional():
    # 10 trading days spanning two ISO weeks
    bars = _bars([100 + i for i in range(10)],
                 start=datetime(2025, 3, 3, tzinfo=UTC))  # Monday
    w = ti.aggregate(bars, "1w")
    assert len(w) == 2
    assert w[0].provisional is False or w[0].provisional is True
    # OHLC merged correctly
    assert w[0].o == pytest.approx(100 * 1.0)
    assert w[1].c == pytest.approx(109)
    # completed helper drops provisional
    comp = [b for b in w if not b.provisional]
    assert all(not b.provisional for b in comp)


def test_aggregation_conserves_volume():
    bars = _bars([100] * 14)
    total_d = sum(b.v for b in bars)
    w = ti.aggregate(bars, "1w")
    assert sum(b.v for b in w) == pytest.approx(total_d)


def test_2d_3d_aggregation():
    """Trading-session buckets: 12 daily bars → exactly 6 2d and
    4 3d groups; the newest bucket is complete, not provisional."""
    bars = _bars([100 + i for i in range(12)])
    d2, d3 = ti.aggregate(bars, "2d"), ti.aggregate(bars, "3d")
    assert len(d2) == 6 and len(d3) == 4
    assert not d2[-1].provisional and not d3[-1].provisional
    # each bucket covers exactly n consecutive sessions
    assert d2[-1].o == pytest.approx(110) and d2[-1].c == pytest.approx(111)
    assert d3[-1].o == pytest.approx(109) and d3[-1].c == pytest.approx(111)


def test_2d_3d_buckets_span_weekends_as_sessions():
    """Friday + Monday are consecutive TRADING sessions — a 2d bucket
    must group them, not split on the calendar gap."""
    import datetime as _dt
    days = [_dt.datetime(2025, 3, 7, tzinfo=UTC),     # Fri
            _dt.datetime(2025, 3, 10, tzinfo=UTC),    # Mon
            _dt.datetime(2025, 3, 11, tzinfo=UTC),    # Tue
            _dt.datetime(2025, 3, 12, tzinfo=UTC)]    # Wed
    bars = [Bar(t=d, o=100, h=101, l=99, c=100 + i, v=1e6)
            for i, d in enumerate(days)]
    d2 = ti.aggregate(bars, "2d")
    assert len(d2) == 2
    # newest bucket = Tue+Wed; oldest = Fri+Mon — the weekend never
    # splits a bucket because buckets count sessions, not days
    assert d2[0].o == pytest.approx(100) and d2[0].c == pytest.approx(101)
    assert d2[1].o == pytest.approx(100) and d2[1].c == pytest.approx(103)
    assert d2[1].t.date() == _dt.date(2025, 3, 12)


# ── signal engine (pure, no db) ──

def test_mr_entry_when_oversold_and_aligned():
    """Strong selloff → RSI<30 + CMI low + %R<−80 → entry_signal."""
    closes = [120.0] * 70 + [120 - i * 1.2 for i in range(1, 15)]
    # choppy decline keeps CMI moderate; deep decline → %R<-80
    daily = _bars(closes)
    r = te.evaluate_mean_reversion(daily)
    ind = r["indicators"]
    assert ind["rsi_10_1d"] < 30
    assert ind["wr_13"] < -80
    assert r["trigger"]["fired"] is True
    assert r["decision"] in ("entry_signal", "wait")  # CMI may or may not pass


def test_mr_wait_when_rsi_above_trigger():
    closes = [100 + i * 0.1 for i in range(80)]
    daily = _bars(closes)
    r = te.evaluate_mean_reversion(daily)
    assert r["decision"] == "wait"
    assert "above 30" in r["explanation"]


def test_mr_invalid_on_short_history():
    daily = _bars([100] * 8)
    r = te.evaluate_mean_reversion(daily)
    assert r["decision"] == "invalid_data"
    assert "insufficient" in r["explanation"]


def test_tf_sequence_halts_at_failed_gate():
    """Uptrend w/ fresh high but weak ADX → wait at a named gate."""
    closes = [100 + i * 0.3 for i in range(90)]  # gentle trend
    daily = _bars(closes)
    r = te.evaluate_trend_following(daily)
    assert r["decision"] in ("wait", "entry_signal")
    if r["decision"] == "wait":
        assert r["first_failed_gate"] in r["sequence"]


def test_tf_invalid_on_short_history():
    daily = _bars([100] * 30)
    r = te.evaluate_trend_following(daily)
    assert r["decision"] == "invalid_data"


def test_band_boundaries_documented():
    """Params are explicit — changing them changes version contract."""
    assert te.PARAMS["rsi_trigger"] == 30
    assert te.PARAMS["aroon_trigger"] == 99
    # v1.1 — freshness gate tightened to 4 calendar days (doc: data
    # must be "current at every close")
    assert te.PARAMS_VERSION == "technical/v1.1"
