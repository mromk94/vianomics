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
METHODOLOGY_VERSION = "rule1-dcf/v1.0"


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
