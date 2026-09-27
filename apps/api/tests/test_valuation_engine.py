"""Valuation engine — independently hand-calculated cases."""

import pytest

from app.services import valuation_engine as ve


def test_sticker_price_hand_calc():
    """EPS=4, g=15%, n=10, PE=30, r=15%, MOS=50%.
    future_eps = 4×1.15^10 = 16.182
    future_price = 16.182×30 = 485.47
    sticker = 485.47/1.15^10 = 120.0
    buy = 60.0"""
    r = ve.sticker_price(4, 0.15, 10, 30, 0.15, 0.5)
    assert r["future_eps"] == pytest.approx(16.182, abs=0.01)
    assert r["sticker_price"] == pytest.approx(120.0, rel=0.01)
    assert r["buy_price"] == pytest.approx(60.0, rel=0.01)


def test_sticker_rejects_bad_inputs():
    with pytest.raises(ValueError):
        ve.sticker_price(-1, 0.15, 10, 30)           # negative EPS
    with pytest.raises(ValueError):
        ve.sticker_price(4, 0.15, 10, 30, mos=1.2)   # MOS > 1


def test_dcf_hand_calc():
    """FCF=100, g=10%, 5y, r=10%, gt=3%, net_debt=0, shares=10.
    PV flows: 110/1.1 +121/1.21 +133.1/1.331 +146.41/1.4641
              +161.051/1.61051 = 5×100 = 500 (g=r → PV=FCF each year)
    TV = 161.051×1.03/(0.10-0.03) = 2370.75 ; PV(TV)=2370.75/1.61051=1472.02
    EV = 1972.02 → per share 197.20"""
    r = ve.dcf(100, 0.10, 5, 0.10, 0.03, 0, 10)
    assert r["pv_flows"] == pytest.approx(500, rel=0.01)
    assert r["terminal_value"] == pytest.approx(2370.75, rel=0.01)
    assert r["per_share"] == pytest.approx(197.20, rel=0.01)


def test_dcf_equity_bridge():
    """net debt shifts EV→equity: same firm, nd=200, shares=10
    → equity = EV−200 → per share = 197.20−20 = 177.20"""
    r = ve.dcf(100, 0.10, 5, 0.10, 0.03, 200, 10)
    assert r["per_share"] == pytest.approx(177.20, rel=0.01)


def test_dcf_invalid_when_r_le_gterminal():
    with pytest.raises(ValueError):
        ve.dcf(100, 0.10, 5, 0.03, 0.03, 0, 10)
    with pytest.raises(ValueError):
        ve.dcf(100, 0.10, 5, 0.025, 0.03, 0, 10)


def test_dcf_negative_fcf_still_computes():
    """Negative FCF → negative IV — surfaced honestly, not clamped."""
    r = ve.dcf(-50, 0.05, 5, 0.10, 0.02, 0, 10)
    assert r["per_share"] < 0


def test_reverse_dcf_recovers_growth():
    """Solve for g given known price from forward run."""
    fwd = ve.dcf(100, 0.12, 5, 0.10, 0.03, 0, 10)
    price = fwd["per_share"]
    rev = ve.reverse_dcf(price, 100, 5, 0.10, 0.03, 0, 10)
    assert rev.get("solved") is True
    assert rev["implied_growth"] == pytest.approx(0.12, abs=0.01)


def test_reverse_dcf_extreme_price():
    rev = ve.reverse_dcf(10000, 100, 5, 0.10, 0.03, 0, 10)
    assert "above best case" in rev["note"]


def test_sensitivity_grid_shape():
    s = ve.sensitivity(100, [0.05, 0.10], [0.08, 0.10], 5, 0.03, 0, 10)
    assert len(s["grid"]) == 2 and len(s["grid"][0]) == 2
    # higher growth → higher IV; higher discount → lower IV
    assert s["grid"][1][0] > s["grid"][0][0]
    assert s["grid"][0][0] > s["grid"][0][1]


def test_five_numbers():
    f = ve.five_numbers(0.15, 0.20, 0.12, 0.18, 0.25)
    assert f["all_pass"] is True
    f2 = ve.five_numbers(0.15, 0.05, 0.12, 0.18, 0.25)
    assert f2["all_pass"] is False and f2["eps_growth"]["pass"] is False
    f3 = ve.five_numbers(0.15, None, 0.12, 0.18, 0.25)
    assert f3["eps_growth"]["value"] is None and f3["all_pass"] is False


def test_margin_of_safety():
    m = ve.margin_of_safety(68, 100)
    assert m["discount"] == pytest.approx(0.32) and m["underpriced"]
    with pytest.raises(ValueError):
        ve.margin_of_safety(50, -10)
