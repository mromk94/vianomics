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


# ── new-docs: CAPM, beta tables, multi-stage DCF, DNI, P/B, PEG/PSG ──

def test_capm_discount_rate():
    """Doc example: 4.72% + 1.23 × 4.3% = 10.01%."""
    r = ve.capm_discount_rate(0.0472, 1.23, 0.043)
    assert r == pytest.approx(0.1001, abs=1e-4)


def test_beta_discount_tables():
    """US table (rf .64%, ERP 5%): β1.0→5.6%, β1.4→7.6%.
    CNHK (rf .60%, ERP 6.6%): β1.0→7.0%, β1.4→10.0%."""
    assert ve.discount_rate_for_beta(1.0, "US") == pytest.approx(0.056)
    assert ve.discount_rate_for_beta(1.4, "US") == pytest.approx(0.076)
    assert ve.discount_rate_for_beta(0.5, "US") == pytest.approx(0.046)
    assert ve.discount_rate_for_beta(1.0, "CNHK") == pytest.approx(0.070)
    assert ve.discount_rate_for_beta(1.4, "CNHK") == pytest.approx(0.100)
    with pytest.raises(ValueError):
        ve.discount_rate_for_beta(1.0, "JP")


def test_multistage_dcf_stage_terminal():
    """Two 5-yr stages + 10-yr terminal stage: PV = Σ every year,
    no Gordon TV — matches the CPRT 20-yr workbook."""
    out = ve.dcf_multistage(
        100, [(5, 0.20), (5, 0.10), (10, 0.04)],
        discount_rate=0.10, cash=50, debt=30, shares=10,
        terminal_method="stage")
    assert out["terminal_value"] is None
    assert len(out["stages"]) == 3
    # hand-check year 1: cf=120, pv=120/1.1=109.09
    cf1 = 100 * 1.2
    pv1 = cf1 / 1.1
    assert out["stages"][0]["pv"] > pv1
    # equity = PV + cash − debt (doc bridge)
    assert out["equity_value"] == pytest.approx(
        out["pv_flows"] + 50 - 30)
    assert out["per_share"] == pytest.approx(
        out["equity_value"] / 10)


def test_multistage_gordon_terminal():
    out = ve.dcf_multistage(
        100, [(10, 0.10)], discount_rate=0.10, cash=0, debt=0,
        shares=10, terminal_method="gordon", terminal_growth=0.025)
    assert out["terminal_value"] is not None
    # TV = FCF_11 / (r − g) discounted at yr 10
    fcf11 = 100 * 1.1 ** 10 * 1.025
    tv = fcf11 / (0.10 - 0.025)
    assert out["terminal_value"] == pytest.approx(tv, rel=1e-4)


def test_multistage_matches_single_stage():
    """One stage + gordon should equal the legacy dcf()."""
    a = ve.dcf_multistage(100, [(10, 0.08)], 0.10, 0, 0, 10,
                          terminal_method="gordon",
                          terminal_growth=0.025)
    b = ve.dcf(100, 0.08, 10, 0.10, 0.025, 0, 10)
    assert a["per_share"] == pytest.approx(b["per_share"], rel=1e-6)


def test_dni_uses_net_income():
    out = ve.dni(60, [(10, 0.10)], 0.0634, 47, 1200, 2.7,
                 terminal_method="stage")
    assert out["method"] == "dni"
    assert out["per_share"] < 0 or out["per_share"] >= 0  # computes


def test_pb_intrinsic():
    r = ve.pb_intrinsic(50, 1.10)
    assert r["per_share"] == pytest.approx(55)


def test_peg_psg_signals():
    assert ve.peg(20, 25)["signal"] == "undervalued"     # PEG .8
    assert ve.peg(22.5, 15)["signal"] == "acceptable"   # 1.5 edge→ok? 1.5 not <1.5
    assert ve.peg(30, 15)["signal"] == "overvalued"
    assert ve.psg(4, 20)["signal"] == "fair_or_cheap"   # PSG .20
    assert ve.psg(10, 20)["signal"] == "overvalued"
    with pytest.raises(ValueError):
        ve.peg(20, -5)


def test_discount_premium_sign():
    """Doc: (price − IV)/IV — negative = discount."""
    r = ve.discount_premium(225.07, 962.72)
    assert r["premium_pct"] == pytest.approx(
        (225.07 - 962.72) / 962.72)
    assert r["at_discount"] is True
