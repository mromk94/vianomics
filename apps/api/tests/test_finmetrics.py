"""Hand-calculated metric tests — every expected value computed manually."""

from decimal import Decimal

import pytest

from app.services import finmetrics as fm

D = Decimal


def test_growth_basic():
    # 100 → 120 = +20%
    assert fm.growth(100, 120) == D("0.2")


def test_growth_negative_base_undefined():
    # -50 → 100 is NOT +300% — base ≤ 0 → None
    assert fm.growth(-50, 100) is None
    assert fm.growth(0, 50) is None


def test_growth_decline():
    assert fm.growth(200, 150) == D("-0.25")


def test_roe_average_equity():
    # NI 20, equity 100→140: avg 120 → 16.67%
    r = fm.roe(20, 100, 140)
    assert r is not None and abs(r - D("0.1666666666666666666666666667")) < D("0.001")


def test_roe_negative_equity_none():
    assert fm.roe(10, -50) is None


def test_roic():
    # EBIT 100, tax 10 / pretax 90 → eff rate ~11.1% → NOPAT 88.9
    # IC = debt 200 + equity 800 − cash 100 = 900 → ROIC ~9.88%
    r = fm.roic(100, 10, 90, 200, 800, 100)
    assert r is not None and abs(r - D("0.09876543")) < D("0.001")


def test_current_ratio():
    assert fm.current_ratio(300, 150) == D("2")
    assert fm.current_ratio(100, 0) is None


def test_debt_ebitda_negative_ebitda():
    # EBIT -50 → EBITDA negative → None, not a huge ratio
    assert fm.debt_to_ebitda(500, -50, 30) is None
    assert fm.debt_to_ebitda(300, 100, 50) == D("2")


def test_interest_coverage_no_debt():
    # no interest expense → N/A, not "infinite coverage"
    assert fm.interest_coverage(100, 0) is None
    assert fm.interest_coverage(100, 20) == D("5")


def test_dscr():
    # OCF 150 / (interest 20 + current debt 30) = 3.0
    assert fm.debt_service_coverage(150, 20, 30) == D("3")
    assert fm.debt_service_coverage(150, 0, 0) is None  # no debt service


def test_fcf_negative():
    # OCF 80 − capex 100 = −20 (negative FCF must surface, not clamp)
    assert fm.fcf(80, 100) == D("-20")


def test_price_ratio_invalid():
    assert fm.price_ratio(150, -2) is None
    assert fm.price_ratio(150, 5) == D("30")


def test_intrinsic_discount():
    # price 68 vs IV 100 → 32% discount
    assert fm.intrinsic_discount(68, 100) == D("0.32")
    assert fm.intrinsic_discount(68, None) is None


def test_trend_ok():
    assert fm.trend_ok([1, 2, 3], increasing=True) is True
    assert fm.trend_ok([3, 2, 3], increasing=True) is False
    assert fm.trend_ok([1, 2], increasing=True) is None  # <3 pts
    assert fm.trend_ok([3, 2, 1], increasing=False) is True
