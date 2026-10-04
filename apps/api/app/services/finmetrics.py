"""Financial metric foundation (Part 4).

Conventions:
- Pure functions over Decimal/None. No DB access, no floats for money.
- Missing or invalid inputs return None — never coerced, never zeroed.
- `growth(prev, curr)`: (curr - prev) / |prev|. Returns None when the
  base is ≤ 0 (growth vs. a loss is undefined, not "infinite").
- Period alignment is the caller's job: pass FY/TTM-consistency-checked
  series; helpers in `series.py` enforce alignment.
- Sector variants are explicit (e.g. `roe_standard` vs bank-adjusted) —
  never silently swap denominators.
"""

from decimal import Decimal
from typing import Iterable

D = Decimal


def _d(v) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


def growth(prev, curr) -> Decimal | None:
    """YoY growth. Undefined when prev <= 0."""
    p, c = _d(prev), _d(curr)
    if p is None or c is None or p <= 0:
        return None
    return (c - p) / abs(p)


def margin(numerator, denominator) -> Decimal | None:
    """e.g. operating margin = operating_income / revenue."""
    n, d = _d(numerator), _d(denominator)
    if n is None or d is None or d == 0:
        return None
    return n / d


def roe(net_income, equity_begin, equity_end=None) -> Decimal | None:
    """ROE = net income / average equity (ending if only one period).
    Returns None when equity ≤ 0 (negative-equity ROE is meaningless)."""
    ni, b, e = _d(net_income), _d(equity_begin), _d(equity_end)
    if ni is None or b is None:
        return None
    eq = (b + e) / 2 if e is not None else b
    if eq <= 0:
        return None
    return ni / eq


def nopat(operating_income, tax_expense, pretax_income) -> Decimal | None:
    """NOPAT = EBIT × (1 − effective tax rate), rate clamped 0–35%."""
    ebit, tax, pretax = (
        _d(operating_income), _d(tax_expense), _d(pretax_income)
    )
    if ebit is None:
        return None
    rate = D("0.21")  # statutory fallback
    if tax is not None and pretax is not None and pretax > 0:
        rate = max(D(0), min(D("0.35"), tax / pretax))
    return ebit * (1 - rate)


def invested_capital(total_debt, equity, cash) -> Decimal | None:
    """IC = total debt + equity − cash."""
    d, e, c = _d(total_debt), _d(equity), _d(cash)
    if d is None and e is None:
        return None
    return (d or D(0)) + (e or D(0)) - (c or D(0))


def roic(operating_income, tax_expense, pretax_income, total_debt, equity, cash) -> Decimal | None:
    n = nopat(operating_income, tax_expense, pretax_income)
    ic = invested_capital(total_debt, equity, cash)
    if n is None or ic is None or ic <= 0:
        return None
    return n / ic


def current_ratio(current_assets, current_liabilities) -> Decimal | None:
    a, l = _d(current_assets), _d(current_liabilities)
    if a is None or l is None or l <= 0:
        return None
    return a / l


def debt_to_ebitda(total_debt, operating_income, depreciation_amortization) -> Decimal | None:
    """Debt/EBITDA where EBITDA = EBIT + D&A.
    Returns None when EBITDA ≤ 0 — ratio is meaningless, not 'infinite'."""
    td, ebit, da = (
        _d(total_debt), _d(operating_income), _d(depreciation_amortization)
    )
    if td is None or ebit is None:
        return None
    ebitda = ebit + (da or D(0))
    if ebitda <= 0:
        return None
    return td / ebitda


def interest_coverage(operating_income, interest_expense) -> Decimal | None:
    """EBIT / interest. None when interest ≤ 0 (no debt cost → N/A)."""
    e, i = _d(operating_income), _d(interest_expense)
    if e is None or i is None or i <= 0:
        return None
    return e / i


def debt_service_coverage(operating_cash_flow, interest_expense, current_debt) -> Decimal | None:
    """DSCR = OCF / (interest + current portion of debt).
    `current_debt` may legitimately be zero; total debt service must be >0."""
    ocf, i, cd = (
        _d(operating_cash_flow), _d(interest_expense), _d(current_debt)
    )
    if ocf is None:
        return None
    service = (i or D(0)) + (cd or D(0))
    if service <= 0:
        return None
    return ocf / service


def fcf(operating_cash_flow, capex) -> Decimal | None:
    o, c = _d(operating_cash_flow), _d(capex)
    if o is None:
        return None
    return o - (c or D(0))


def per_share(value, shares) -> Decimal | None:
    v, s = _d(value), _d(shares)
    if v is None or s is None or s <= 0:
        return None
    return v / s


def price_ratio(price, per_share_value) -> Decimal | None:
    """P/X = price / per-share value. None when X ≤ 0."""
    p, v = _d(price), _d(per_share_value)
    if p is None or v is None or v <= 0:
        return None
    return p / v


def intrinsic_discount(price, intrinsic_value) -> Decimal | None:
    """(IV − price) / IV. None without a valuation."""
    p, iv = _d(price), _d(intrinsic_value)
    if p is None or iv is None or iv <= 0:
        return None
    return (iv - p) / iv


def shares_change(prev, curr) -> Decimal | None:
    """Signed share-count change; negative = buybacks (good)."""
    return growth(prev, curr)  # same math; callers read the sign


def trend_ok(series: Iterable, increasing: bool, min_points: int = 3) -> bool | None:
    """Monotone-ish trend check: each point improves vs previous.
    None = insufficient history."""
    vals = [v for v in series if v is not None]
    if len(vals) < min_points:
        return None
    diffs = [vals[i] - vals[i - 1] for i in range(1, len(vals))]
    return all((d >= 0 if increasing else d <= 0) for d in diffs)


def beta(stock_returns, bench_returns, min_points: int = 26):
    """OLS market beta — cov(r_i, r_b) / var(r_b) over paired
    returns. None when the sample is too thin or the benchmark is
    flat (variance 0). Callers pair aligned periods; this never
    fabricates a beta from unmatched data."""
    rs = [(float(a), float(b)) for a, b in zip(stock_returns,
                                              bench_returns)
          if a is not None and b is not None]
    if len(rs) < min_points:
        return None
    mx = sum(b for _, b in rs) / len(rs)      # bench mean
    my = sum(a for a, _ in rs) / len(rs)      # stock mean
    var = sum((b - mx) ** 2 for _, b in rs)
    if var <= 0:
        return None
    cov = sum((a - my) * (b - mx) for a, b in rs)
    return cov / var
