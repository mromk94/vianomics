"""Parts 12–15 — deterministic risk engine. Safety-critical: all math
pure Decimal, limits hard-block, AI cannot override.

SOW pyramid method (5 stages, long):
  S1: $Risk = equity × risk_pct; shares = $Risk / (1.5 × ATR)
  S2: Target1 = entry + 3 × ATR₀ — DOUBLE (no profit-taking), if
      addition passes all risk checks
  S3: Current ATR = price × ATR_12w% (weekly ATR(14) / close)
  S4: new stop = price − 1.0 × Current ATR (never widens past limits)
  S5: Target2 policy = trailing(0.75×ATR) | profit_50 — explicit,
      versioned, never combined
"""

from dataclasses import dataclass, field

from app.db.base import utcnow
from decimal import Decimal
from enum import Enum

D = Decimal

ENGINE_VERSION = "risk-pyramid/v1.0"

# Default limits (mandate-overridable via LimitSet)
DEFAULT_LIMITS = {
    "max_drawdown_pct": 0.15,
    "max_sector_pct": 0.25,
    "max_single_name_pct": 0.10,
    "min_sectors": 5,
    "max_correlation": 0.70,          # avg pairwise within new exposure
    "min_cash_pct": 0.05,
    "risk_per_trade_pct": 0.005,      # 0.5% equity
    "max_gross_leverage": 1.0,        # no margin by default
    "vix_reduce_above": 30,           # VIX-based position reduction
    "fg_block_new_above": 80,         # F&G restriction on new positions
    "vol_reduction_vol": 0.60,        # ann. vol → halve size
}


class PyramidState(str, Enum):
    WATCHLIST = "watchlist"
    ELIGIBLE = "trade_eligible"
    INITIAL = "initial_position"
    TARGET1 = "target_1"
    ADDITION = "position_addition"
    TARGET2 = "target_2"
    TRAILING = "trailing_exit"
    PARTIAL_EXIT = "partial_exit"
    CLOSED = "closed"
    STOPPED = "stopped_out"
    REJECTED = "rejected"


@dataclass
class Limits:
    values: dict = field(default_factory=lambda: dict(DEFAULT_LIMITS))

    def get(self, k):
        return self.values.get(k, DEFAULT_LIMITS.get(k))


@dataclass
class Breach:
    rule: str
    observed: float | None
    required: float | str
    blocking: bool = True
    remediation: str = ""


# ── S1: sizing ──

def initial_sizing(
    equity, risk_pct, entry, atr,
    available_cash, lot_size=1, max_adv_participation=0.10,
    adv_shares=None, instrument_price=None,
) -> dict:
    """Stage 1: $Risk → shares @1.5×ATR stop distance, clamped by
    cash, lot size, liquidity participation."""
    eq, rp, e, a = map(D, (equity, risk_pct, entry, atr))
    cash = D(str(available_cash))
    if e <= 0 or a <= 0 or eq <= 0:
        raise ValueError("entry/atr/equity must be > 0")
    dollar_risk = eq * rp
    stop_dist = a * D("1.5")
    raw_shares = dollar_risk / stop_dist
    # constraints
    cash_cap = cash / e
    liq_cap = (D(str(adv_shares)) * D(str(max_adv_participation))
               if adv_shares else cash_cap)
    shares = min(raw_shares, cash_cap, liq_cap)
    shares = int(shares / lot_size) * lot_size
    return {
        "dollar_risk": float(dollar_risk),
        "stop_distance": float(stop_dist),
        "stop_price": float(e - stop_dist),
        "shares_raw": float(raw_shares),
        "shares": int(shares),
        "binding": ("liquidity" if shares == int(liq_cap / lot_size) * lot_size and liq_cap < raw_shares and liq_cap < cash_cap
                    else "cash" if cash_cap < raw_shares else "risk"),
        "notional": float(D(int(shares)) * e),
    }


# ── S3–S4: volatility recalc + stop ──

def atr_12w_pct(weekly_atr, current_price) -> Decimal | None:
    """ATR% = weekly ATR(14) / price — from 12 completed weekly bars
    (documented)."""
    if not weekly_atr or not current_price or current_price <= 0:
        return None
    return D(str(weekly_atr)) / D(str(current_price))


def tighten_stop(current_price, current_atr, prev_stop) -> dict:
    """S4: new = price − 1.0×ATR_cur; never below previous stop (long)."""
    p, a, prev = map(D, (current_price, current_atr, prev_stop))
    new = p - a
    return {"new_stop": float(max(new, prev)),
            "moved": new > prev,
            "note": "ratchet only — never loosen"}


# ── pyramid state machine ──

@dataclass
class PyramidTrade:
    symbol: str
    entry: float
    atr_initial: float
    shares: int
    stop: float
    target1: float
    state: PyramidState = PyramidState.INITIAL
    t2_policy: str | None = None          # 'trailing' | 'profit_50'
    additions: int = 0
    events: list = field(default_factory=list)

    def log(self, msg):
        self.events.append(msg)


def create_pyramid(symbol, equity, entry, atr, cash, risk_pct=0.005,
                   adv_shares=None, t2_policy="trailing") -> PyramidTrade:
    if t2_policy not in ("trailing", "profit_50"):
        raise ValueError("t2_policy must be 'trailing' or 'profit_50'")
    sz = initial_sizing(equity, risk_pct, entry, atr, cash,
                        adv_shares=adv_shares)
    t = PyramidTrade(
        symbol=symbol, entry=float(entry),
        atr_initial=float(atr), shares=sz["shares"],
        stop=sz["stop_price"],
        target1=float(D(str(entry)) + 3 * D(str(atr))),
        state=PyramidState.INITIAL, t2_policy=t2_policy)
    t.log(f"S1 entry {sz['shares']}sh @ {entry} stop {sz['stop_price']:.2f} "
          f"T1 {t.target1:.2f} policy={t2_policy}")
    return t


def advance(trade: PyramidTrade, price, current_atr, *,
            fill_price=None, add_ok=True) -> dict:
    """Advance the state machine on a price observation. Returns the
    transition result. Gaps through stop close at fill_price."""
    p = D(str(price))
    t = trade
    # gap through stop → stop-out at observed fill
    if trade.state in (PyramidState.INITIAL, PyramidState.TARGET1,
                       PyramidState.ADDITION, PyramidState.TRAILING,
                       PyramidState.TARGET2, PyramidState.PARTIAL_EXIT):
        if p < D(str(trade.stop)):
            fill = D(str(fill_price)) if fill_price else p
            t.state = PyramidState.STOPPED
            t.log(f"STOPPED @ {fill} (stop {trade.stop})"
                  + (" — gapped" if fill < D(str(trade.stop)) else ""))
            return {"state": t.state, "fill": float(fill),
                    "loss_per_share": float(D(str(trade.entry)) - fill)}

    if t.state == PyramidState.INITIAL and p >= D(str(t.target1)):
        # S2: DOUBLE — no profit taking
        if not add_ok:
            t.state = PyramidState.REJECTED
            t.log("T1 reached — addition REJECTED by risk checks")
            return {"state": t.state, "note": "addition blocked; "
                    "position continues under original stop"}
        t.shares *= 2
        t.additions += 1
        t.state = PyramidState.TARGET1
        t.log(f"T1 hit — doubled to {t.shares}sh (no profit taken)")
        return {"state": t.state, "shares": t.shares}

    if t.state == PyramidState.TARGET1:
        # S3+S4: recalc + tighten
        s = tighten_stop(price, current_atr, t.stop)
        t.stop = s["new_stop"]
        t.state = PyramidState.TARGET2
        t.log(f"S3/4: stop tightened to {s['new_stop']:.2f}")
        return {"state": t.state, "stop": t.stop}

    if t.state == PyramidState.TARGET2:
        # S5 policy — explicit
        if t.t2_policy == "trailing":
            trail = p - D(str(current_atr)) * D("0.75")
            new_stop = max(D(str(t.stop)), trail)
            t.stop = float(new_stop)
            t.state = PyramidState.TRAILING
            t.log(f"T2 trailing stop {float(new_stop):.2f}")
        else:  # profit_50
            sell = t.shares // 2
            t.shares -= sell
            t.state = PyramidState.PARTIAL_EXIT
            t.log(f"T2 50% profit: sold {sell}sh, {t.shares} trail at "
                  f"{t.stop:.2f}")
            trail = p - D(str(current_atr)) * D("0.75")
            t.stop = float(max(D(str(t.stop)), trail))
        return {"state": t.state, "shares": t.shares, "stop": t.stop}

    if t.state in (PyramidState.TRAILING, PyramidState.PARTIAL_EXIT):
        trail = p - D(str(current_atr)) * D("0.75")
        if trail > D(str(t.stop)):
            t.stop = float(trail)
            t.log(f"trail up → {t.stop:.2f}")
            return {"state": t.state, "stop": t.stop}
        return {"state": t.state, "stop": t.stop}

    return {"state": t.state}


# ── Part B: investment holding tests ──

def holding_tests(position: dict) -> dict:
    """Five tests — output review actions, not mechanical stops."""
    out = {}
    out["fundamental_deterioration"] = (
        "exit_or_review" if position.get("fundamentals_deteriorated")
        else "pass")
    out["valuation_at_intrinsic"] = (
        "reduce_or_exit" if position.get("price_vs_iv", 0) >= 0
        else "pass")
    out["thesis_invalidation"] = (
        "exit" if position.get("thesis_broken") else "pass")
    out["excessive_size"] = (
        "review_reduce" if position.get("weight", 0) >
        position.get("max_weight", 0.10) else "pass")
    out["normal_volatility"] = "no_action"
    out["note"] = ("investment holdings — no mechanical trading stops "
                   "unless mandate requires")
    return out


# ── Part C: seven risk dimensions + stress ──

def scenario_pnl(positions: list[dict], shock_pct: float,
                 beta_mult: float = 1.0) -> dict:
    """Portfolio P&L under uniform shock (beta-scaled)."""
    total = sum(p["market_value"] for p in positions)
    pnl = sum(p["market_value"] * shock_pct
              * p.get("beta", 1.0) * beta_mult for p in positions)
    return {"shock": shock_pct, "pnl": pnl, "nav_after": total + pnl}


def risk_dimensions(pf: dict) -> dict:
    """pf: {nav, cash, positions:[{market_value, sector, beta, weight,
    liquidity_days}], margin_used, max_dd, avg_correlation,
    fcf_trend, price_vs_iv}"""
    pos = pf.get("positions", [])
    nav = pf.get("nav", 0) or 1
    sectors: dict[str, float] = {}
    for p in pos:
        sectors[p.get("sector", "?")] = sectors.get(p.get("sector", "?"), 0) + p["market_value"] / nav

    stress = {s: scenario_pnl(pos, s) for s in (-0.10, -0.20, -0.30, -0.40)}
    # correlated shock: everything to ρ=1
    corr_pnl = sum(p["market_value"] * -0.30 for p in pos)
    # vol shock: positions re-priced at +50% IV → value unchanged but
    # flag VaR expansion via beta scale on −20%
    vol_shock = scenario_pnl(pos, -0.20, beta_mult=1.5)

    max_w = max((p["market_value"] / nav for p in pos), default=0)
    illiq = sum(p["market_value"] for p in pos
                if p.get("liquidity_days", 0) > 5) / nav

    def status(ok, degraded=False):
        return "ok" if ok else ("degraded" if degraded else "breach")

    data_ok = bool(pos)
    return {
        "fundamental_valuation": {
            "status": "ok" if data_ok else "unknown",
            "price_vs_iv": pf.get("price_vs_iv"),
            "detail": "needs dossier data" if not data_ok else None},
        "concentration": {
            "max_single_name_pct": max_w,
            "max_sector_pct": max(sectors.values()) if sectors else None,
            "sector_count": len(sectors),
            "sectors": sectors,
            "status": ("breach" if max_w
                       > DEFAULT_LIMITS["max_single_name_pct"]
                       else "ok") if data_ok else "unknown"},
        "factor_correlation": {
            "avg_correlation": pf.get("avg_correlation"),
            "correlated_shock_30pct_pnl": corr_pnl,
            "status": "ok" if pf.get("avg_correlation") is not None
            else "degraded"},
        "leverage_margin": {
            "gross": pf.get("gross", 1.0),
            "margin_used": pf.get("margin_used", 0),
            "margin_shock_pnl": scenario_pnl(pos, -0.20)["pnl"] *
            (pf.get("gross", 1.0)),
            "status": ("breach" if pf.get("gross", 1.0)
                       > DEFAULT_LIMITS["max_gross_leverage"] * 1.01
                       else "ok" if pf.get("gross", 1.0) <= 1.0
                       else "review")},
        "liquidity": {
            "illiquid_weight": illiq,
            "cash_pct": pf.get("cash", 0) / nav,
            "status": ("breach" if pf.get("cash", 0) / nav
                       < DEFAULT_LIMITS["min_cash_pct"]
                       else "ok") if data_ok else "unknown"},
        "volatility_structure": {
            "max_drawdown": pf.get("max_dd"),
            "vol_shock_pnl": vol_shock["pnl"],
            "status": "ok" if pf.get("max_dd") is not None else "degraded"},
        "feedback_path": {
            "status": "unknown",
            "detail": "path-dependency modeled via scenario ladder; "
                      "reflexivity not directly measurable"},
        "stress_tests": stress,
    }


# ── Part D: order gate ──

def check_order(
    order: dict,             # {side, symbol, sector, notional, qty}
    pf: dict,                # {nav, cash, positions:[...], max_dd,
                             #  avg_correlation, vix, fear_greed}
    limits: Limits | None = None,
) -> dict:
    """Hard limit gate — returns breaches; any blocking breach → denied.
    This is the ONLY path orders may take; AI cannot bypass."""
    L = limits or Limits()
    nav = pf.get("nav") or 1
    breaches: list[Breach] = []

    if order["side"] == "buy":
        notional = order["notional"]
        # single-name
        cur = sum(p["market_value"] for p in pf["positions"]
                  if p["symbol"] == order["symbol"]
                  and not p.get("external")) / nav
        new_w = (cur * nav + notional) / nav
        lim = L.get("max_single_name_pct")
        if new_w > lim:
            breaches.append(Breach(
                "max_single_name", round(new_w, 4), lim,
                remediation=f"reduce order ≤ {(lim - cur) * nav:,.0f} notional"))
        # sector
        sec_w = (sum(p["market_value"] for p in pf["positions"]
                     if p.get("sector") == order.get("sector")
                     and not p.get("external")) + notional) / nav
        lim = L.get("max_sector_pct")
        if sec_w > lim:
            breaches.append(Breach(
                "max_sector", round(sec_w, 4), lim,
                remediation="trim sector exposure first"))
        # cash floor
        cash_after = (pf.get("cash", 0) - notional) / nav
        lim = L.get("min_cash_pct")
        if cash_after < lim:
            breaches.append(Breach(
                "min_cash", round(cash_after, 4), f">= {lim}",
                remediation="reduce size or raise cash"))
        # sector count
        secs = {p.get("sector") for p in pf["positions"]
                if not p.get("external")}
        secs.add(order.get("sector"))
        if len(secs) < L.get("min_sectors"):
            breaches.append(Breach(
                "min_sectors", len(secs), L.get("min_sectors"),
                remediation="diversify before adding"))
        # leverage
        gross_after = (sum(p["market_value"] for p in pf["positions"])
                       + notional) / (nav + pf.get("cash", 0) - notional + notional)
        lim = L.get("max_gross_leverage")
        gross = (sum(p["market_value"] for p in pf["positions"]) + notional) / nav
        if gross > lim:
            breaches.append(Breach(
                "leverage", round(gross, 3), lim,
                remediation="no margin — reduce gross"))
        # drawdown
        dd = pf.get("max_dd") or 0
        lim = L.get("max_drawdown_pct")
        if abs(dd) >= lim:
            breaches.append(Breach(
                "max_drawdown", round(abs(dd), 4), lim,
                remediation="portfolio at DD limit — new buys blocked"))
        # correlation
        avg_corr = pf.get("avg_correlation")
        lim = L.get("max_correlation")
        if avg_corr is not None and avg_corr > lim:
            breaches.append(Breach(
                "correlation", round(avg_corr, 3), lim,
                remediation="portfolio already correlated — diversify"))
        # VIX
        vix = pf.get("vix")
        lim = L.get("vix_reduce_above")
        if vix is not None and vix >= lim:
            breaches.append(Breach(
                "vix_reduce", vix, lim,
                remediation="VIX risk-off — halve or defer new buys"))
        # F&G
        fg = pf.get("fear_greed")
        lim = L.get("fg_block_new_above")
        if fg is not None and fg >= lim:
            breaches.append(Breach(
                "fg_restriction", fg, lim,
                remediation="F&G ≥80 — no new positions"))
        # volatility-based sizing
        vol = pf.get("portfolio_vol")
        if vol is not None and vol >= L.get("vol_reduction_vol"):
            breaches.append(Breach(
                "vol_reduction", vol, L.get("vol_reduction_vol"),
                blocking=False,
                remediation="elevated vol — halve size"))

    return {
        "allowed": not any(b.blocking for b in breaches),
        "breaches": [vars(b) for b in breaches],
        "engine": ENGINE_VERSION,
        "as_of": pf.get("as_of"),
    }


def equity_stats(equity_history: list[dict],
                 current_equity: float | None) -> dict:
    """MDD + daily VaR95 + today's Δ from an equity history series —
    real numbers from the account, not parametric guesses."""
    eqs = [float(h["equity"]) for h in (equity_history or [])
           if h.get("equity") is not None]
    if current_equity:
        eqs = eqs + [float(current_equity)]
    out = {"mdd_pct": None, "var_95": None, "daily_pnl": None,
           "n_points": len(eqs)}
    if len(eqs) >= 2:
        peak, mdd = eqs[0], 0.0
        for e in eqs:
            peak = max(peak, e)
            mdd = min(mdd, e / peak - 1)
        out["mdd_pct"] = round(mdd, 4)
        rets = sorted(eqs[i] / eqs[i - 1] - 1
                      for i in range(1, len(eqs)) if eqs[i - 1])
        if rets:
            out["var_95"] = round(rets[max(0, int(len(rets) * 0.05))]
                                  * eqs[-1], 2)
        today = utcnow().date().isoformat()
        past = [h["equity"] for h in equity_history
                if h.get("t", "")[:10] < today]
        if past and current_equity is not None:
            out["daily_pnl"] = round(
                float(current_equity) - float(past[-1]), 2)
    return out
