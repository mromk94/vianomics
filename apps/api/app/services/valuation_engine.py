"""Part 7 — Deterministic valuation engine.

Rule #1 (Phil Town convention):
  future_eps   = eps * (1 + g)^n
  future_price = future_eps * future_pe   (pe = min(2*g*100, hist_pe_cap))
  sticker      = future_price / (1 + required_return)^n
  buy_price    = sticker * (1 - mos)

DCF (Gordon terminal):
  PV = Σ FCF_t*(1+g)^t / (1+r)^t  +  TV/(1+r)^n
  TV = FCF_n*(1+g_t) / (r - g_t)
  equity = EV - net_debt ;  per share = equity / diluted_shares

Reverse DCF: bisect growth g so DCF(price) solves — implied growth.

All pure Decimal. Invalid configs raise ValueError — never silently
clamp. METHODOLOGY_VERSION is bumped when formulas change so stored
runs stay reproducible.
"""

from decimal import Decimal

D = Decimal
METHODOLOGY_VERSION = "rule1-dcf/v2.0"


def _d(v) -> Decimal:
    return Decimal(str(v))


# ── Rule #1 ────────────────────────────────────────────────────────

def five_numbers(
    rev_growth, eps_growth, equity_growth, fcf_growth, roic,
    growth_min=D("0.10"), roic_min=D("0.15"),
) -> dict:
    """Rule #1 Five Numbers — each pass/fail + values."""
    checks = {
        "revenue_growth": rev_growth,
        "eps_growth": eps_growth,
        "equity_growth": equity_growth,
        "fcf_growth": fcf_growth,
    }
    out = {}
    for k, v in checks.items():
        out[k] = {"value": None if v is None else float(v),
                  "pass": v is not None and v > growth_min}
    out["roic"] = {"value": None if roic is None else float(roic),
                   "pass": roic is not None and roic > roic_min}
    out["all_pass"] = all(c["pass"] for c in out.values())
    return out


def sticker_price(
    eps, growth, years: int, future_pe, required_return=D("0.15"),
    mos=D("0.50"),
) -> dict:
    """Rule #1 Sticker & Buy Price."""
    eps, g, pe, r, m = map(_d, (eps, growth, future_pe,
                              required_return, mos))
    if eps <= 0:
        raise ValueError("EPS must be > 0 for Rule #1 sticker price")
    if g <= -1 or r <= 0:
        raise ValueError("invalid growth/discount rate")
    if not 0 < m < 1:
        raise ValueError("margin of safety must be in (0,1)")
    future_eps = eps * (1 + g) ** years
    future_price = future_eps * pe
    sticker = future_price / (1 + r) ** years
    buy = sticker * (1 - m)
    return {
        "future_eps": float(future_eps),
        "future_price": float(future_price),
        "sticker_price": float(sticker),
        "buy_price": float(buy),
        "assumptions": {
            "eps": float(eps), "growth": float(g), "years": years,
            "future_pe": float(pe), "required_return": float(r),
            "mos": float(m),
        },
    }


def default_future_pe(growth, hist_pe_cap=D("40")) -> Decimal:
    """Town convention: future PE = min(2×growth%, historical cap)."""
    return min(_d(growth) * 100 * 2, _d(hist_pe_cap))


# ── DCF ────────────────────────────────────────────────────────────

def dcf(
    base_fcf,
    growth,
    years: int,
    discount_rate,
    terminal_growth,
    net_debt,
    shares,
) -> dict:
    """Per-share DCF with EV→equity bridge."""
    fcf, g, r, gt, nd, sh = map(
        _d, (base_fcf, growth, discount_rate, terminal_growth,
             net_debt, shares))
    if r <= gt:
        raise ValueError(
            "discount rate must exceed terminal growth "
            f"({float(r)} vs {float(gt)}) — else TV diverges")
    if sh <= 0:
        raise ValueError("shares outstanding must be > 0")
    if years < 1:
        raise ValueError("projection years must be ≥ 1")

    flows = []
    pv_flows = D(0)
    for t in range(1, years + 1):
        ft = fcf * (1 + g) ** t
        pv = ft / (1 + r) ** t
        pv_flows += pv
        flows.append({"year": t, "fcf": float(ft), "pv": float(pv)})

    tv = fcf * (1 + g) ** years * (1 + gt) / (r - gt)
    pv_tv = tv / (1 + r) ** years
    ev = pv_flows + pv_tv
    equity_val = ev - nd
    per_share = equity_val / sh

    return {
        "enterprise_value": float(ev),
        "pv_flows": float(pv_flows),
        "terminal_value": float(tv),
        "pv_terminal_value": float(pv_tv),
        "net_debt": float(nd),
        "equity_value": float(equity_val),
        "per_share": float(per_share),
        "flows": flows,
        "assumptions": {
            "base_fcf": float(fcf), "growth": float(g), "years": years,
            "discount_rate": float(r), "terminal_growth": float(gt),
        },
    }


def reverse_dcf(
    price, base_fcf, years, discount_rate, terminal_growth,
    net_debt, shares, tol=D("0.0005"), max_iter=80,
) -> dict:
    """Implied growth: solve g such that DCF per-share == price."""
    p = _d(price)
    if p <= 0:
        raise ValueError("price must be > 0")

    def iv(g):
        try:
            return _d(dcf(base_fcf, g, years, discount_rate,
                          terminal_growth, net_debt, shares)["per_share"])
        except ValueError:
            return None

    lo, hi = D("-0.5"), D("1.0")
    ivlo, ivhi = iv(lo), iv(hi)
    if ivlo is None or ivhi is None:
        raise ValueError("invalid DCF config for reverse solve")
    if p < ivlo:
        return {"implied_growth": float(lo), "note": "price below worst case"}
    if p > ivhi:
        return {"implied_growth": float(hi), "note": "price above best case"}

    for _ in range(max_iter):
        mid = (lo + hi) / 2
        if abs(iv(mid) - p) < tol:
            break
        if iv(mid) < p:
            lo = mid
        else:
            hi = mid
    return {"implied_growth": float((lo + hi) / 2), "solved": True}


def sensitivity(
    base_fcf, growths, discount_rates, years, terminal_growth,
    net_debt, shares,
) -> dict:
    """per-share IV grid over growth × discount rate."""
    grid = []
    for g in growths:
        row = []
        for r in discount_rates:
            try:
                row.append(dcf(base_fcf, g, years, r, terminal_growth,
                               net_debt, shares)["per_share"])
            except ValueError:
                row.append(None)
        grid.append(row)
    return {
        "growths": [float(g) for g in growths],
        "discount_rates": [float(r) for r in discount_rates],
        "grid": grid,
    }


def margin_of_safety(price, intrinsic_value) -> dict:
    """(IV − P)/IV — negative when overpriced."""
    p, iv = _d(price), _d(intrinsic_value)
    if iv <= 0:
        raise ValueError("intrinsic value must be > 0")
    d = (iv - p) / iv
    return {"price": float(p), "intrinsic_value": float(iv),
            "discount": float(d), "underpriced": d > 0}


# ── Discount rate: CAPM + beta lookup tables (DCF Methods doc) ────

def capm_discount_rate(risk_free, beta, market_risk_premium) -> float:
    """Cost of equity = rf + β × ERP — doc Part 1.
    Example: 4.72% + 1.23 × 4.3% = 10.01%."""
    rf, b, erp = _d(risk_free), _d(beta), _d(market_risk_premium)
    return float(rf + b * erp)


# Doc "DCF Calculator & Stock Valuation" — discount-rate lookup by
# beta. US: rf 0.64%, ERP 5.00% · China/HK: rf 0.60%, ERP 6.60%.
_BETA_TABLES = {
    "US": [(0.80, 0.046), (1.00, 0.056), (1.10, 0.061), (1.20, 0.066),
           (1.30, 0.071), (1.40, 0.076), (1.50, 0.081),
           (float("inf"), 0.086)],
    "CNHK": [(0.80, 0.059), (1.00, 0.070), (1.10, 0.079), (1.20, 0.090),
             (1.30, 0.092), (1.40, 0.100), (1.50, 0.105),
             (float("inf"), 0.110)],
}


def discount_rate_for_beta(beta, market: str = "US") -> float:
    """Beta → discount rate via the doc's lookup table (US or CNHK)."""
    table = _BETA_TABLES.get(market.upper())
    if table is None:
        raise ValueError(f"unknown market {market!r} — use US|CNHK")
    b = float(beta)
    for ceiling, rate in table:
        if b <= ceiling or ceiling == float("inf"):
            return rate
    return table[-1][1]


# ── Multi-stage DCF / DNI (DCF Methods + CPRT workbook) ───────────

def dcf_multistage(
    base_cf,
    stages: list[tuple[int, float]],
    discount_rate,
    cash,
    debt,
    shares,
    terminal_method: str = "stage",
    terminal_growth=None,
) -> dict:
    """Multi-stage DCF — the doc/workbook canonical form.

    stages: [(n_years, growth), ...] e.g. [(5, .33), (5, .50), (10, .04)]
      terminal_method="stage"  → last stage IS the terminal window
                               (CPRT 20-yr sheet: sum PV of all years)
      terminal_method="gordon" → Gordon TV appended after last stage
    Equity bridge per doc: PV + cash − debt; ÷ shares.
    """
    if terminal_method not in ("stage", "gordon"):
        raise ValueError("terminal_method must be 'stage' or 'gordon'")
    cf, r, c_, d_, sh = map(_d, (base_cf, discount_rate, cash, debt,
                               shares))
    if sh <= 0:
        raise ValueError("shares outstanding must be > 0")
    if not stages:
        raise ValueError("at least one growth stage required")
    if r <= 0:
        raise ValueError("discount rate must be > 0")

    year = 0
    pv_total = D(0)
    stage_breakdown = []
    cur = cf
    for i, (n, g) in enumerate(stages):
        n = int(n)
        if n < 1:
            raise ValueError("stage years must be ≥ 1")
        g = _d(g)
        pv_stage = D(0)
        for _ in range(n):
            year += 1
            cur = cur * (1 + g)
            pv = cur / (1 + r) ** year
            pv_stage += pv
        pv_total += pv_stage
        stage_breakdown.append({
            "stage": i + 1, "years": n, "growth": float(g),
            "end_cf": float(cur), "pv": float(pv_stage)})

    tv = pv_tv = D(0)
    if terminal_method == "gordon":
        gt = _d(terminal_growth if terminal_growth is not None else 0)
        if r <= gt:
            raise ValueError(
                "discount rate must exceed terminal growth — "
                "else TV diverges")
        tv = cur * (1 + gt) / (r - gt)
        pv_tv = tv / (1 + r) ** year
        pv_total += pv_tv

    equity_val = pv_total + c_ - d_
    per_share = equity_val / sh
    return {
        "pv_flows": float(pv_total - pv_tv),
        "terminal_value": float(tv) if terminal_method == "gordon"
        else None,
        "pv_terminal_value": float(pv_tv)
        if terminal_method == "gordon" else None,
        "stages": stage_breakdown,
        "cash": float(c_), "debt": float(d_),
        "equity_value": float(equity_val),
        "per_share": float(per_share),
        "assumptions": {
            "base_cf": float(cf), "discount_rate": float(r),
            "years": year, "terminal_method": terminal_method,
        },
    }


def dni(base_net_income, stages, discount_rate, cash, debt, shares,
        **kw) -> dict:
    """Discounted Net Income — same mechanics as multi-stage DCF on
    net income. Doc: for financial stocks where NI grows more
    consistently than operating cash flow."""
    out = dcf_multistage(base_net_income, stages, discount_rate,
                         cash, debt, shares, **kw)
    out["method"] = "dni"
    return out


def pb_intrinsic(bvps, fair_pb) -> dict:
    """Banks: IV = BVPS × fair P/B (doc: fair band 1.00–1.20)."""
    bv, m = _d(bvps), _d(fair_pb)
    if bv <= 0:
        raise ValueError("book value per share must be > 0")
    return {"bvps": float(bv), "fair_pb": float(m),
            "per_share": float(bv * m)}


# ── Quick-check ratios (doc: PEG + PSG) ───────────────────────────

def peg(pe, earnings_growth_pct) -> dict:
    """PEG = PE ÷ earnings-growth% — doc: <1 undervalued, <1.5 OK."""
    p, g = _d(pe), _d(earnings_growth_pct)
    if g <= 0:
        raise ValueError("earnings growth must be > 0 — PEG only "
                         "valid when earnings consistently increase")
    v = p / g
    return {"peg": float(v), "pe": float(p), "growth_pct": float(g),
            "signal": ("undervalued" if v < 1
                       else "acceptable" if v <= D("1.5")
                       else "overvalued")}


def psg(price_to_sales, revenue_growth_pct) -> dict:
    """PSG = P/S ÷ revenue-growth% — doc fair value 0.20."""
    ps, g = _d(price_to_sales), _d(revenue_growth_pct)
    if g <= 0:
        raise ValueError("revenue growth must be > 0")
    v = ps / g
    return {"psg": float(v), "p_to_s": float(ps),
            "revenue_growth_pct": float(g),
            "signal": "fair_or_cheap" if v <= D("0.20")
            else "overvalued"}


def discount_premium(price, intrinsic_value) -> dict:
    """Doc convention: (price − IV)/IV — negative = discount."""
    p, iv = _d(price), _d(intrinsic_value)
    if iv <= 0:
        raise ValueError("intrinsic value must be > 0")
    v = (p - iv) / iv
    return {"price": float(p), "intrinsic_value": float(iv),
            "premium_pct": float(v),
            "at_discount": v < 0}
