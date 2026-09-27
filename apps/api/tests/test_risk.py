"""Parts 12–15 — risk engine: sizing, pyramid machine, limits gate.

Hand-calculated. The gate test verifies a 'perfect research' order
still gets blocked by risk — signals never override limits.
"""

import pytest

from app.services import risk_engine as re_
from app.services.risk_engine import PyramidState as S


# ── S1 sizing ──

def test_sizing_hand_calc():
    """equity 1M, risk 0.5% → $5k; entry 100, ATR 4 → stop dist 6,
    raw 833 shares → cash cap 1M/100=10k; shares=833."""
    s = re_.initial_sizing(1_000_000, 0.005, 100, 4, 1_000_000)
    assert s["stop_price"] == pytest.approx(94)
    assert s["shares"] == 833
    assert s["binding"] == "risk"


def test_sizing_cash_binding():
    s = re_.initial_sizing(1_000_000, 0.005, 100, 4, 10_000)
    assert s["shares"] == 100  # 10k cash / 100
    assert s["binding"] == "cash"


def test_sizing_liquidity_binding():
    # ADV 5000 × 10% = 500 share cap
    s = re_.initial_sizing(1_000_000, 0.005, 100, 4, 1_000_000,
                           adv_shares=5000)
    assert s["shares"] == 500
    assert s["binding"] == "liquidity"


def test_sizing_rejects_invalid():
    with pytest.raises(ValueError):
        re_.initial_sizing(0, 0.005, 100, 4, 1e6)
    with pytest.raises(ValueError):
        re_.initial_sizing(1e6, 0.005, 0, 4, 1e6)


# ── pyramid machine ──

def _trade(**kw):
    return re_.create_pyramid("X", 1_000_000, 100, 4, 1_000_000, **kw)


def test_pyramid_full_lifecycle_trailing():
    t = _trade(t2_policy="trailing")
    assert t.state == S.INITIAL and t.target1 == pytest.approx(112)

    # T1 at 112 → double, no profit
    r = re_.advance(t, 112, 5.0)
    assert t.state == S.TARGET1 and t.shares == 833 * 2
    assert "no profit" in t.events[-1]

    # S3/S4 tighten: stop = 115 − 1×5 = 110 (ratchet above old 94)
    r = re_.advance(t, 115, 5.0)
    assert t.state == S.TARGET2 and t.stop == pytest.approx(110)

    # T2 trailing: 118 − 0.75×5 = 114.25
    r = re_.advance(t, 118, 5.0)
    assert t.state == S.TRAILING and t.stop == pytest.approx(114.25)

    # trail up further: 122 − 3.75 = 118.25
    re_.advance(t, 122, 5.0)
    assert t.stop == pytest.approx(118.25)

    # price falls to 117 < stop 118.25 → stopped out
    r = re_.advance(t, 117, 5.0)
    assert t.state == S.STOPPED


def test_pyramid_profit_50_policy():
    t = _trade(t2_policy="profit_50")
    re_.advance(t, 112, 5.0)              # T1 → double
    re_.advance(t, 115, 5.0)              # tighten
    before = t.shares
    re_.advance(t, 118, 5.0)              # T2 → sell half
    assert t.state == S.PARTIAL_EXIT
    assert t.shares == before // 2


def test_t2_policy_must_be_explicit():
    with pytest.raises(ValueError):
        _trade(t2_policy="combine_both")  # silent mixing rejected


def test_addition_rejected_when_risk_fails():
    t = _trade()
    r = re_.advance(t, 112, 5.0, add_ok=False)
    assert t.state == S.REJECTED
    assert t.shares == 833  # no doubling


def test_gap_through_stop():
    t = _trade()
    # price gaps below stop 94 → fills at 90
    r = re_.advance(t, 92, 5.0, fill_price=90)
    assert t.state == S.STOPPED
    assert r["fill"] == 90
    assert "gapped" in t.events[-1]


def test_stop_never_loosens():
    t = _trade()
    re_.advance(t, 112, 5.0)
    re_.advance(t, 115, 5.0)              # stop → 110
    # price drops to 113, ATR 8 → new = 105 < 110 → keep 110
    r = re_.tighten_stop(113, 8, 110)
    assert r["new_stop"] == 110


def test_atr_12w_pct():
    assert float(re_.atr_12w_pct(6.0, 200)) == pytest.approx(0.03)
    assert re_.atr_12w_pct(None, 200) is None


# ── Part B holding tests ──

def test_holding_tests_no_mechanical_stops():
    out = re_.holding_tests({"weight": 0.15, "max_weight": 0.10,
                             "price_vs_iv": -0.3})
    assert out["excessive_size"] == "review_reduce"
    assert out["normal_volatility"] == "no_action"
    assert "no mechanical" in out["note"]


# ── Part D order gate ──

def _pf(**over):
    base = {
        "nav": 1_000_000, "cash": 200_000,
        "positions": [
            {"symbol": "AAA", "sector": "Tech", "market_value": 80_000},
            {"symbol": "BBB", "sector": "Health", "market_value": 70_000},
            {"symbol": "CCC", "sector": "Indus", "market_value": 70_000},
            {"symbol": "DDD", "sector": "Energy", "market_value": 60_000},
            {"symbol": "EEE", "sector": "Staples", "market_value": 60_000},
        ],
        "max_dd": -0.05, "avg_correlation": 0.4,
        "vix": 18, "fear_greed": 50,
    }
    base.update(over)
    return base


def test_order_passes_when_limits_ok():
    r = re_.check_order(
        {"symbol": "FFF", "side": "buy", "sector": "Materials",
         "notional": 50_000}, _pf())
    assert r["allowed"] is True


def test_single_name_limit_blocks():
    r = re_.check_order(
        {"symbol": "AAA", "side": "buy", "sector": "Tech",
         "notional": 50_000}, _pf())  # 80k + 50k = 130k > 10% of 1M
    assert r["allowed"] is False
    rules = [b["rule"] for b in r["breaches"]]
    assert "max_single_name" in rules


def test_sector_limit_blocks():
    r = re_.check_order(
        {"symbol": "GGG", "side": "buy", "sector": "Tech",
         "notional": 200_000}, _pf())  # 80k+200k=280k > 25%
    assert not r["allowed"]
    assert "max_sector" in [b["rule"] for b in r["breaches"]]


def test_all_green_signals_but_risk_blocks():
    """Every research signal positive — F&G 85 blocks new positions."""
    pf = _pf(fear_greed=85)
    r = re_.check_order(
        {"symbol": "HOT", "side": "buy", "sector": "Tech",
         "notional": 40_000}, pf)
    assert r["allowed"] is False
    assert "fg_restriction" in [b["rule"] for b in r["breaches"]]


def test_vix_risk_off_blocks():
    r = re_.check_order(
        {"symbol": "X", "side": "buy", "sector": "Tech",
         "notional": 30_000}, _pf(vix=32))
    assert not r["allowed"]
    assert "vix_reduce" in [b["rule"] for b in r["breaches"]]


def test_drawdown_limit_blocks():
    r = re_.check_order(
        {"symbol": "X", "side": "buy", "sector": "Tech",
         "notional": 30_000}, _pf(max_dd=-0.16))
    assert not r["allowed"]
    assert "max_drawdown" in [b["rule"] for b in r["breaches"]]


def test_min_cash_blocks():
    r = re_.check_order(
        {"symbol": "X", "side": "buy", "sector": "Tech",
         "notional": 190_000}, _pf())  # cash 200k→10k = 1% < 5%
    assert not r["allowed"]
    assert "min_cash" in [b["rule"] for b in r["breaches"]]


def test_leverage_blocks():
    r = re_.check_order(
        {"symbol": "X", "side": "buy", "sector": "Materials",
         "notional": 800_000}, _pf())  # gross >1 → breach
    assert not r["allowed"]
    assert "leverage" in [b["rule"] for b in r["breaches"]]


def test_sell_never_blocked():
    r = re_.check_order(
        {"symbol": "AAA", "side": "sell", "sector": "Tech",
         "notional": 50_000}, _pf(fear_greed=95, vix=45, max_dd=-0.5))
    assert r["allowed"] is True  # risk-off never traps exits


def test_stale_unknown_inputs_degrade_not_fail():
    dims = re_.risk_dimensions({"nav": 1e6, "positions": []})
    assert dims["factor_correlation"]["status"] in ("degraded", "ok")
    assert dims["feedback_path"]["status"] == "unknown"


def test_stress_ladder():
    pf = _pf()
    dims = re_.risk_dimensions(pf)
    assert set(dims["stress_tests"]) == {-0.10, -0.20, -0.30, -0.40}
    assert dims["stress_tests"][-0.30]["pnl"] == pytest.approx(
        -0.3 * (80e3 + 70e3 + 70e3 + 60e3 + 60e3))


def test_breach_carries_remediation():
    r = re_.check_order(
        {"symbol": "AAA", "side": "buy", "sector": "Tech",
         "notional": 50_000}, _pf())
    b = next(b for b in r["breaches"] if b["rule"] == "max_single_name")
    assert b["observed"] and b["required"] and b["remediation"]
