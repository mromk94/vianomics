"""Quant stats + regime classification — hand-verified."""

import math
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.market import MacroObservation, MacroSeries, OhlcvBar
from app.models.instruments import Instrument
from app.models.macro import RegimeRun
from app.services import quant as q
from app.services import macro_regime as mr


# ── quant stats ──

def test_returns_simple():
    assert q.returns([100, 110, 121]) == pytest.approx([0.10, 0.10])


def test_ann_vol_known():
    # daily rets ±1% alternating → daily sd ~1.005% → ann ~15.96%
    rets = [0.01, -0.01] * 50
    v = q.volatility(rets)
    assert v == pytest.approx(0.1596, abs=0.001)


def test_sharpe_known():
    # constant 0.1% daily → ar = 1.001^252-1 ≈ 28.65%; vol→0 → None
    assert q.sharpe([0.001] * 100) is None
    rets = [0.002 if i % 2 else 0.0 for i in range(100)]
    s = q.sharpe(rets, rf_annual=0.0)
    assert s is not None and s > 0


def test_sortino_downside_only():
    up = [0.02] * 100
    mixed = [0.04, -0.01] * 50
    s_up, s_mx = q.sortino(up, 0), q.sortino(mixed, 0)
    assert s_up is None  # no downside → undefined (dv=0 → None)
    assert s_mx is not None


def test_max_drawdown_known():
    # 100→120→60→110: peak 120 trough 60 = −50%
    m = q.max_drawdown([100, 120, 60, 110])
    assert m["max_drawdown"] == pytest.approx(-0.5)
    assert (m["from_idx"], m["to_idx"]) == (1, 2)


def test_beta_known():
    # asset = 2× market + 0 → β=2, ρ=1
    mkt = [0.01, -0.02, 0.03, -0.01, 0.02, -0.015, 0.005]
    ast = [2 * r for r in mkt]
    assert q.beta(ast, mkt)["beta"] == pytest.approx(2.0)
    assert q.correlation(ast, mkt)["rho"] == pytest.approx(1.0)


def test_momentum_12_1():
    # flat 100 for 253 days then linear ramp → last 21 days rising
    prices = [100.0] * 232 + [100 + i for i in range(22)]
    m = q.momentum_12_1(prices)
    assert m == pytest.approx(prices[-22] / 100 - 1, abs=0.001)


def test_factor_scores_missing_disclosed():
    s = q.factor_scores({"momentum": 80, "quality": 60})
    assert s["coverage"] == pytest.approx(0.4)
    assert s["composite"] is not None
    assert set(s["weights_used"]) == {"momentum", "quality"}
    assert s["subscores"]["value"] is None


def test_portfolio_exposure():
    pos = [
        {"weight": 0.6, "subscores": {"momentum": 80, "quality": 60}},
        {"weight": 0.4, "subscores": {"momentum": 40, "quality": None}},
    ]
    e = q.portfolio_exposure(pos)
    assert e["momentum"] == pytest.approx(64)   # .6*80+.4*40
    assert e["quality"] == pytest.approx(60)    # only first has quality


def test_event_study():
    # stock +0.1/day, bench +0.05/day → per-day AR ≈ +0.0005
    prices = [100 + i * 0.1 for i in range(50)]
    bench = [100 + i * 0.05 for i in range(50)]
    r = q.event_study(prices, bench, [25], window=3)
    assert r["events"] == 1 and len(r["mean_ar"]) == 7
    assert r["cum_mean_ar"] == pytest.approx(0.0034, abs=0.001)
    assert all(x > 0 for x in r["mean_ar"] if x is not None)


# ── regime bands ──

def test_vix_bands_boundaries():
    assert mr.vix_band(14.99) == "normal"
    assert mr.vix_band(15.0) == "tighten_risk"      # lower-bound inclusive
    assert mr.vix_band(19.99) == "tighten_risk"
    assert mr.vix_band(20.0) == "reduce_position_size"
    assert mr.vix_band(30.0) == "risk_off"
    assert mr.vix_band(None) is None


def test_fg_bands_boundaries():
    assert mr.fear_greed_overlay(24.9) == "accumulation"
    assert mr.fear_greed_overlay(25) == "cautious_accumulation"
    assert mr.fear_greed_overlay(45) == "neutral"
    assert mr.fear_greed_overlay(65) == "reduce"
    assert mr.fear_greed_overlay(80) == "aggressive_risk_reduction"
    assert mr.fear_greed_overlay(100) == "aggressive_risk_reduction"


def test_sector_rotation_map():
    assert mr.SECTOR_ROTATION["recovery"] == [
        "Information Technology", "Consumer Discretionary", "Industrials"]
    assert mr.SECTOR_ROTATION["recession"] == [
        "Consumer Staples", "Utilities", "Health Care"]


# ── regime classify on fixture data ──

async def _macro(db, code, vals):
    s = MacroSeries(source="fred", code=code, name=code)
    db.add(s)
    await db.flush()
    for d, v in vals:
        db.add(MacroObservation(
            series_id=s.id, value=v, source="fred",
            observed_at=d, published_at=d))


async def test_regime_recession_rule(db):
    as_of = datetime(2025, 6, 1, tzinfo=UTC)
    # HY spread blowout → recession
    await _macro(db, "BAMLH0A0HYM2", [
        (as_of - timedelta(days=20), 6.5),
        (as_of - timedelta(days=10), 7.0),
        (as_of, 7.2)])
    await _macro(db, "T10Y2Y", [(as_of, -0.5)])
    await _macro(db, "UNRATE", [(as_of - timedelta(90), 5.5),
                                (as_of, 6.2)])
    await _macro(db, "INDPRO", [(as_of - timedelta(180), 100),
                                (as_of, 99)])
    await db.commit()
    c = await mr.classify(db, as_of)
    assert c["econ_regime"] == "recession"
    assert any("hy_spread" in h for h in c["rule_hits"])


async def test_regime_insufficient_when_no_data(db):
    c = await mr.classify(db, datetime(2025, 6, 1, tzinfo=UTC))
    assert c["econ_regime"] == "insufficient_data"
    assert "INDPRO" in c["stale_inputs"]


async def test_regime_pit_respects_asof(db):
    """Observations after as_of must not leak into classification."""
    as_of = datetime(2025, 3, 1, tzinfo=UTC)
    await _macro(db, "BAMLH0A0HYM2", [
        (as_of - timedelta(30), 3.0),          # calm before
        (as_of + timedelta(30), 9.0)])          # spike after as_of
    await _macro(db, "UNRATE", [(as_of - timedelta(30), 4.0)])
    await _macro(db, "INDPRO", [(as_of - timedelta(30), 100)])
    await db.commit()
    c = await mr.classify(db, as_of)
    assert c["features"]["BAMLH0A0HYM2"]["value"] == 3.0
    assert c["econ_regime"] != "recession"


async def test_regime_run_persisted_reproducible(db):
    as_of = datetime(2025, 6, 1, tzinfo=UTC)
    await _macro(db, "T10Y2Y", [(as_of, 0.5)])
    await _macro(db, "VIXCLS", [(as_of, 16.0)])
    await db.commit()
    r = await mr.run_and_persist(db, as_of)
    await db.commit()
    assert r.rules_version == mr.RULES_VERSION
    assert r.features["VIXCLS"]["value"] == 16.0
    assert r.vix_band == "tighten_risk"
