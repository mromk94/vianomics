"""Deterministic risk engine. Safety-critical: all math pure
Decimal, limits hard-block, AI cannot override.

VAIIP Revised Pyramid — Adaptive Trailing Protocol (new-docs
canonical, Layer IV Part 12):
  Entry: stop = close − 1.5 × ATR₀, target = close + 3 × ATR₀
  Maintenance loop (every timeframe close):
    pull current ATR → stop := max(stop, close − 1.5 × current ATR)
    — ratchet only, NEVER loosen
  Target hit → ADD an equal-size leg (subject to risk re-check);
    next target = close + 3 × current ATR; the single trailing stop
    applies to the whole pyramid
  Stop hit → EXIT ALL
"""

from dataclasses import dataclass, field

from app.db.base import utcnow
from decimal import Decimal
from enum import Enum

D = Decimal

ENGINE_VERSION = "risk-pyramid/v2.0"

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
    # ── VAIIP new-docs risk hierarchy (workbook Settings sheet) ──
    "max_trade_risk_pct": 0.02,       # per-trade risk $ ≤ 2% equity
    "max_asset_risk_pct": 0.05,       # per-asset open risk ≤ 5% equity
    "max_portfolio_open_risk_pct": 0.15,  # open stop risk ≤ 15% equity
    "max_strategy_risk_pct": 0.10,    # per-strategy open risk ≤ 10%
    "max_margin_utilisation_pct": 0.50,   # used margin ≤ 50% equity
    "warn_drawdown_pct": 0.05,        # DD escalation: NORMAL→WATCH
    "reduce_drawdown_pct": 0.10,      # → RISK REDUCTION
    "halt_drawdown_pct": 0.15,        # → TRADING HALT
    "min_rr": 2.0,                    # soft floor on reward/risk
    "max_positions": None,            # None = uncapped
    "starter_fraction": None,         # None = full-size initial legs
    "external_margin_rate": 0.20,     # margin rate for external CFD
                                    # positions w/o instrument match
    # ── Layer-IV trading sleeve (V2 doc Phase 0) — the ATR pyramid
    # engine runs only inside this 30% risk bucket. Off by default —
    # flat risk_pct sizing remains until the sleeve is enabled. ──
    "sleeve_enabled": False,
    "sleeve_pct": 0.30,               # sleeve share of total equity
    "sleeve_target_leverage": 5.0,    # gross cap = sleeve_equity × 5
    "sleeve_initial_margin": 0.20,    # broker initial margin rate
    "sleeve_maint_margin": 0.16,      # default; ingest broker rate
    "sleeve_max_positions": 5,
    "sleeve_max_asset_gross_pct": 0.20,   # per-asset ≤ 20% of gross cap
    "sleeve_starter_fraction": 0.25,  # starter = ¼ of max per asset
    "sleeve_portfolio_stop_pct": 0.20,    # 20% sleeve-equity DD → all out
    "sleeve_gross_stop_pct": 0.04,        # 4% of current gross → all out
                                        # (the tighter stop at low
                                        # deployment — doc Phase 0/2.2)
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


def tighten_stop(current_price, current_atr, prev_stop,
                 mult=1.5) -> dict:
    """Adaptive trailing: new = price − mult×ATR_cur; never below the
    previous stop for a long (ratchet only — never loosen)."""
    p, a, prev = map(D, (current_price, current_atr, prev_stop))
    new = p - a * D(str(mult))
    return {"new_stop": float(max(new, prev)),
            "moved": new > prev,
            "note": "ratchet only — never loosen"}


# ── pyramid state machine (revised adaptive trailing) ──

@dataclass
class PyramidTrade:
    symbol: str
    entry: float
    atr_initial: float
    shares: int
    stop: float
    target1: float                        # current target (moves up)
    state: PyramidState = PyramidState.INITIAL
    t2_policy: str | None = None          # legacy v1 field — unused
    additions: int = 0
    leg_shares: int = 0                   # shares per pyramid leg
    atr_current: float | None = None
    # per-leg cost basis — each entry {"fill", "shares", "leg"}; the
    # pyramid's P&L is computed against these fills, NEVER against the
    # leg-1 entry for every share (legs 2+ fill at target prices)
    leg_fills: list = field(default_factory=list)
    events: list = field(default_factory=list)

    def log(self, msg):
        self.events.append(msg)

    @property
    def legs(self) -> int:
        return 1 + self.additions


_OPEN_STATES = (PyramidState.INITIAL, PyramidState.TARGET1,
                PyramidState.ADDITION, PyramidState.TARGET2,
                PyramidState.TRAILING, PyramidState.PARTIAL_EXIT)


def create_pyramid(symbol, equity, entry, atr, cash, risk_pct=0.005,
                   adv_shares=None, t2_policy=None,
                   sizing: dict | None = None) -> PyramidTrade:
    sz = sizing or initial_sizing(equity, risk_pct, entry, atr, cash,
                                  adv_shares=adv_shares)
    t = PyramidTrade(
        symbol=symbol, entry=float(entry),
        atr_initial=float(atr), shares=sz["shares"],
        stop=sz["stop_price"],
        target1=float(D(str(entry)) + 3 * D(str(atr))),
        state=PyramidState.INITIAL, leg_shares=sz["shares"],
        atr_current=float(atr), t2_policy=t2_policy,
        leg_fills=[{"fill": float(entry), "shares": sz["shares"],
                    "leg": 1}])
    t.log(f"entry {sz['shares']}sh @ {entry} — stop "
          f"{sz['stop_price']:.2f} (1.5×ATR), target "
          f"{t.target1:.2f} (3×ATR)")
    return t


def maintain(trade: PyramidTrade, price, current_atr) -> dict:
    """Maintenance loop — run at EVERY timeframe close (spec Part 12):
    recalculate the 1.5× current-ATR stop and tighten if it moves in
    the trade's favour. Never loosens."""
    s = tighten_stop(price, current_atr, trade.stop, mult=1.5)
    if s["moved"]:
        trade.stop = s["new_stop"]
        trade.log(f"maintenance: stop → {s['new_stop']:.2f} "
                  f"(1.5×ATR={current_atr})")
    trade.atr_current = float(current_atr)
    return {"stop": trade.stop, "moved": s["moved"]}


def advance(trade: PyramidTrade, price, current_atr, *,
            fill_price=None, add_ok=True) -> dict:
    """Advance on a price observation (call every timeframe close):
      1. stop hit → EXIT ALL
      2. maintenance loop → ratchet 1.5× current-ATR stop
      3. target hit → ADD a leg (if risk re-check passes) and set the
         next target 3×ATR above current price"""
    p = D(str(price))
    atr = D(str(current_atr))
    t = trade

    if t.state in _OPEN_STATES and p <= D(str(t.stop)):
        fill = D(str(fill_price)) if fill_price else p
        t.state = PyramidState.STOPPED
        t.log(f"STOPPED @ {fill} (stop {t.stop}) — exit all "
              f"{t.shares}sh across {t.legs} leg(s)"
              + (" — gapped" if fill < D(str(t.stop)) else ""))
        return {"state": t.state, "fill": float(fill),
                "loss_per_share": float(D(str(t.entry)) - fill)}

    if t.state not in _OPEN_STATES:
        return {"state": t.state}

    # maintenance loop — every close, always
    maintain(t, price, current_atr)

    # target hit → add a leg (risk re-check still applies)
    if t.target1 is not None and p >= D(str(t.target1)):
        if not add_ok:
            t.log("target reached — addition REJECTED by risk checks; "
                  "pyramid continues under trailing stop")
            return {"state": t.state, "stop": t.stop,
                    "note": "addition blocked"}
        t.shares += t.leg_shares
        t.additions += 1
        t.state = (PyramidState.TARGET1 if t.additions == 1
                   else PyramidState.ADDITION)
        t.target1 = float(p + 3 * atr)
        add_fill = float(fill_price) if fill_price else float(p)
        t.leg_fills.append({"fill": add_fill, "shares": t.leg_shares,
                            "leg": t.legs})
        t.log(f"target hit — added leg {t.legs} (+{t.leg_shares}sh "
              f"@ {add_fill:.2f}, total {t.shares}sh); next target "
              f"{t.target1:.2f} (3×ATR); stop {t.stop:.2f} covers "
              "all legs")
        return {"state": t.state, "shares": t.shares,
                "stop": t.stop, "target": t.target1}

    return {"state": t.state, "stop": t.stop}


# ── Layer-IV trading sleeve (V2 doc Phase 0–2) ──
#
# The ATR pyramid engine runs only inside a designated trading sleeve
# (default 30% of total equity). The sleeve carries its own leverage
# cap, margin ledger, per-asset cap, and portfolio stop — a separate
# risk bucket, not a view of the whole book.

SLEEVE_STRATEGIES = {"atr_pyramid", "sleeve", "trading"}


def sleeve_config(limits: "Limits | None" = None) -> dict:
    L = limits or Limits()
    return {
        "enabled": bool(L.get("sleeve_enabled")),
        "sleeve_pct": float(L.get("sleeve_pct") or 0.30),
        "target_leverage": float(L.get("sleeve_target_leverage") or 5.0),
        "initial_margin": float(L.get("sleeve_initial_margin") or 0.20),
        "maint_margin": float(L.get("sleeve_maint_margin") or 0.16),
        "max_positions": int(L.get("sleeve_max_positions") or 5),
        "max_asset_gross_pct":
            float(L.get("sleeve_max_asset_gross_pct") or 0.20),
        "starter_fraction":
            float(L.get("sleeve_starter_fraction") or 0.25),
        "portfolio_stop_pct":
            float(L.get("sleeve_portfolio_stop_pct") or 0.20),
        "gross_stop_pct":
            float(L.get("sleeve_gross_stop_pct") or 0.04),
    }


def sleeve_state(total_equity, sleeve_gross, cfg: dict,
                 open_positions: int = 0,
                 maint_margin: float | None = None) -> dict:
    """Live sleeve ledger. `sleeve_gross` is the marked gross exposure
    of positions inside the sleeve; `maint_margin` overrides the
    configured rate with the broker-ingested one when available."""
    eq = D(str(total_equity)) * D(str(cfg["sleeve_pct"]))
    gross = D(str(sleeve_gross))
    maint = D(str(maint_margin if maint_margin is not None
                  else cfg["maint_margin"]))
    gross_cap = eq * D(str(cfg["target_leverage"]))
    max_asset = gross_cap * D(str(cfg["max_asset_gross_pct"]))
    starter = max_asset * D(str(cfg["starter_fraction"]))
    port_stop = eq * D(str(cfg["portfolio_stop_pct"]))
    risk_budget = (port_stop / D(str(cfg["max_positions"]))
                   if cfg["max_positions"] else port_stop)
    used_margin = gross * D(str(cfg["initial_margin"]))
    # Margin-call buffer (doc Step 2.3): the portfolio stop must fire
    # BEFORE the broker's maintenance call. After the stop, equity is
    # eq − stop; a call triggers at maint% × gross. Solving for gross:
    #   max_gross_safe = (eq − stop) / maint%
    # Doc example: $30k/20%/5X/16% → $150k cap exactly; a 20% maint
    # rate caps the book at $120k (4X) — exposure must shrink.
    max_gross_safe = ((eq - port_stop) / maint) if maint > 0 \
        else gross_cap
    effective_cap = min(gross_cap, max_gross_safe)
    margin_call_at = (eq / maint) if maint > 0 else None
    return {
        "sleeve_equity": float(eq),
        "gross_cap": float(gross_cap),
        "max_gross_safe": float(max_gross_safe),
        "effective_gross_cap": float(effective_cap),
        # parity counts as capped — the doc requires maint% STRICTLY
        # below the stop boundary; at equality call and stop coincide
        "buffer_capped": max_gross_safe <= gross_cap,
        "maint_margin_used": float(maint),
        "max_asset_notional": float(max_asset),
        "starter_notional": float(starter),
        "portfolio_stop_usd": float(port_stop),
        "per_trade_risk_budget": float(risk_budget),
        "gross": float(gross),
        "used_margin": float(used_margin),
        "free_margin": float(eq - used_margin),
        "margin_utilisation": (float(used_margin / eq)
                               if eq > 0 else None),
        "effective_leverage": (float(gross / eq) if eq > 0 else None),
        "open_positions": open_positions,
        "margin_call_at_gross": (float(margin_call_at)
                                 if margin_call_at else None),
    }


def sleeve_sizing(entry, atr, state: dict, cfg: dict,
                  asset_gross: float = 0.0, adv_shares=None,
                  lot_size=1) -> dict:
    """Doc Step 2.4 — the sleeve constraint solver.

    Final starter shares = min(
        risk-budget shares   = per_trade_risk_budget ÷ 1.5×ATR,
        starter-value shares = starter_notional ÷ price,
        asset-gross headroom = (max_asset_notional − asset_gross) ÷ px,
        sleeve-gross headroom= (effective_cap − gross) ÷ px,
        margin headroom      = free_margin ÷ (px × initial_margin),
        liquidity            = 10% ADV participation, when known)

    Cash is NOT a cap — the sleeve is margin-funded by design; the
    funding constraint is free margin. Gross cap includes the
    maintenance-margin buffer (max_gross_safe) so a hot book can never
    put itself margin-call distance inside the portfolio stop."""
    e, a = D(str(entry)), D(str(atr))
    if e <= 0 or a <= 0:
        raise ValueError("entry/atr must be > 0")
    im = D(str(cfg["initial_margin"]))
    caps: dict[str, Decimal] = {
        "risk_budget": D(str(state["per_trade_risk_budget"]))
                       / (a * D("1.5")),
        "starter_value": D(str(state["starter_notional"])) / e,
        "asset_gross": (D(str(state["max_asset_notional"]))
                        - D(str(asset_gross))) / e,
        "gross_cap": (D(str(state["effective_gross_cap"]))
                      - D(str(state["gross"]))) / e,
        "margin": (D(str(state["free_margin"])) / (e * im)
                   if im > 0 else D("1e18")),
    }
    if adv_shares:
        caps["liquidity"] = D(str(adv_shares)) * D("0.10")
    binding = min(caps, key=lambda k: caps[k])
    shares = int(caps[binding] / lot_size) * lot_size
    shares = max(shares, 0)
    stop = e - a * D("1.5")
    return {
        "shares": shares,
        "shares_raw": float(caps[binding]),
        "binding": binding,
        "caps": {k: float(v) for k, v in caps.items()},
        "dollar_risk": float(D(shares) * a * D("1.5")),
        "stop_price": float(stop),
        "stop_distance": float(a * D("1.5")),
        "notional": float(D(shares) * e),
        "margin_required": float(D(shares) * e * im),
    }


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


def risk_dimensions(pf: dict, limits: "Limits | None" = None) -> dict:
    """pf: {nav, cash, positions:[{market_value, sector, beta, weight,
    liquidity_days}], margin_used, max_dd, avg_correlation,
    fcf_trend, price_vs_iv}"""
    L = limits or Limits()
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
                if (p.get("liquidity_days") or 0) > 5) / nav

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
                       > L.get("max_single_name_pct")
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
                       > L.get("max_gross_leverage") * 1.01
                       else "ok" if pf.get("gross", 1.0) <= 1.0
                       else "review")},
        "liquidity": {
            "illiquid_weight": illiq,
            "cash_pct": pf.get("cash", 0) / nav,
            "status": ("breach" if pf.get("cash", 0) / nav
                       < L.get("min_cash_pct")
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
        notional_d = _d(notional)
        # ── new-docs hard limits (workbook Settings) ──
        # stop validity — spec §24 hard rule
        if order.get("stop_invalid"):
            breaches.append(Breach(
                "stop_invalid", None, "stop on the risk side of entry",
                remediation="fix stop placement before submitting"))
        # portfolio open-stop risk + new trade risk ≤ limit
        open_risk = _d(pf.get("open_risk") or 0)
        trade_risk = _d(order.get("risk_dollars") or 0)
        lim = L.get("max_portfolio_open_risk_pct")
        if lim is not None and open_risk + trade_risk > _d(nav) * _d(lim):
            breaches.append(Breach(
                "portfolio_open_risk",
                round(float(open_risk + trade_risk), 2),
                f"<= {float(_d(nav) * _d(lim)):,.0f}",
                remediation="capacity exhausted — close or tighten "
                            "stops before adding risk"))
        # single-asset open risk — spec §16 hierarchy
        if trade_risk > 0:
            asset_risk = sum(
                _d(p.get("open_risk") or 0)
                for p in pf["positions"]
                if p["symbol"] == order["symbol"]) + trade_risk
            lim = L.get("max_asset_risk_pct")
            if (lim is not None
                    and asset_risk > _d(nav) * _d(lim)):
                breaches.append(Breach(
                    "max_asset_risk", round(float(asset_risk), 2),
                    f"<= {float(_d(nav) * _d(lim)):,.0f}",
                    remediation="asset risk budget exhausted"))
        # strategy open risk — spec §16 hierarchy
        if order.get("strategy") and trade_risk > 0:
            strat_risk = sum(
                _d(p.get("open_risk") or 0)
                for p in pf["positions"]
                if p.get("strategy") == order["strategy"]) + trade_risk
            lim = L.get("max_strategy_risk_pct")
            if (lim is not None
                    and strat_risk > _d(nav) * _d(lim)):
                breaches.append(Breach(
                    "max_strategy_risk", round(float(strat_risk), 2),
                    f"<= {float(_d(nav) * _d(lim)):,.0f}",
                    remediation="strategy risk budget exhausted"))
        # margin utilisation
        used_margin = _d(pf.get("margin_used") or 0)
        new_margin = _d(order.get("margin") or 0)
        lim = L.get("max_margin_utilisation_pct")
        if (lim is not None and used_margin + new_margin
                > _d(nav) * _d(lim)):
            breaches.append(Breach(
                "margin_utilisation",
                round(float(used_margin + new_margin), 2),
                f"<= {float(_d(nav) * _d(lim)):,.0f}",
                remediation="insufficient margin headroom"))
        # insufficient available margin — spec §24 hard rule
        free_margin = _d(nav) - used_margin
        if new_margin > 0 and new_margin > free_margin:
            breaches.append(Breach(
                "insufficient_margin",
                round(float(new_margin), 2),
                f"<= free margin {float(free_margin):,.0f}",
                remediation="order needs more margin than is free"))
        # ── Layer-IV sleeve gates — sleeve-tagged orders are bounded
        # by the sleeve's own caps (gross incl. margin-call buffer,
        # per-asset, position count, free margin). Portfolio stop
        # overrides everything; a capped buffer means the broker's
        # maintenance margin would fire before our own stop — that
        # configuration rejects leverage, not the trade idea. ──
        sleeve = pf.get("sleeve") or {}
        if order.get("sleeve") and sleeve.get("enabled"):
            st = sleeve["state"]
            cfg = sleeve["config"]
            if sleeve.get("cooldown"):
                breaches.append(Breach(
                    "sleeve_cooldown", None,
                    "sleeve in cooldown after portfolio stop",
                    remediation="requires human reassessment — "
                                "release via /risk/sleeve/release"))
            sg = _d(st["gross"])
            if not sleeve.get("cooldown") \
                    and sg + notional_d > _d(st["effective_gross_cap"]):
                breaches.append(Breach(
                    "sleeve_gross_cap",
                    round(float(sg + notional_d), 2),
                    f"<= {st['effective_gross_cap']:,.0f}"
                    + (" (margin-call buffer)"
                       if st["buffer_capped"] else ""),
                    remediation="sleeve gross cap reached — "
                                + ("broker maint margin is tighter "
                                   "than the portfolio stop; reduce "
                                   "leverage" if st["buffer_capped"]
                                   else "close a leg first")))
            asset_g = sum(
                _d(p["market_value"]) for p in sleeve["positions"]
                if p["symbol"] == order["symbol"])
            if asset_g + notional_d > _d(st["max_asset_notional"]):
                breaches.append(Breach(
                    "sleeve_max_asset",
                    round(float(asset_g + notional_d), 2),
                    f"<= {st['max_asset_notional']:,.0f}",
                    remediation="per-asset sleeve cap (20% of gross)"))
            new_pos = order["symbol"] not in {
                p["symbol"] for p in sleeve["positions"]}
            if new_pos and st["open_positions"] >= cfg["max_positions"]:
                breaches.append(Breach(
                    "sleeve_max_positions",
                    st["open_positions"] + 1, cfg["max_positions"],
                    remediation="5-position sleeve cap — close one"))
            order_margin = notional_d * _d(cfg["initial_margin"])
            if order_margin > _d(st["free_margin"]):
                breaches.append(Breach(
                    "sleeve_margin",
                    round(float(order_margin), 2),
                    f"<= free {st['free_margin']:,.0f}",
                    remediation="no sleeve margin headroom"))
        # max position count
        lim = L.get("max_positions")
        if lim is not None and order["symbol"] not in {
                p["symbol"] for p in pf["positions"]}:
            if len(pf["positions"]) + 1 > int(lim):
                breaches.append(Breach(
                    "max_positions", len(pf["positions"]) + 1, lim,
                    remediation="position count cap — close one first"))
        # R/R soft review (non-blocking)
        rr = order.get("rr")
        if rr is not None and rr < float(L.get("min_rr") or 0):
            breaches.append(Breach(
                "min_rr", round(rr, 2), f">= {L.get('min_rr')}",
                blocking=False,
                remediation="R/R below minimum — PM review"))
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
    """Drawdown panel (spec §20/P8) + VaR95 + P&L deltas from a real
    equity history series — not parametric guesses.
    Emits: peak, current, current_dd, daily_dd, weekly_dd,
    monthly_dd, mdd_pct, var_95, daily_pnl."""
    pts = [(str(h.get("t") or ""), float(h["equity"]))
           for h in (equity_history or [])
           if h.get("equity") is not None]
    eqs = [e for _, e in pts]
    if current_equity:
        eqs = eqs + [float(current_equity)]
    out = {"peak_equity": None, "current_equity": None,
           "current_dd_pct": None, "daily_dd_pct": None,
           "weekly_dd_pct": None, "monthly_dd_pct": None,
           "mdd_pct": None, "var_95": None, "daily_pnl": None,
           "n_points": len(eqs)}
    if not eqs:
        return out
    out["current_equity"] = eqs[-1]
    out["peak_equity"] = max(eqs)
    if out["peak_equity"]:
        out["current_dd_pct"] = round(
            eqs[-1] / out["peak_equity"] - 1, 4)
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
    # period drawdowns: peak-within-window → current (Panel 8)
    if pts and current_equity:
        from datetime import timedelta
        now = utcnow()
        for key, days in (("daily_dd_pct", 1), ("weekly_dd_pct", 7),
                          ("monthly_dd_pct", 30)):
            cutoff = (now - timedelta(days=days)).isoformat()
            win = [e for t, e in pts if t >= cutoff]
            win.append(float(current_equity))
            pk = max(win)
            if pk:
                out[key] = round(float(current_equity) / pk - 1, 4)
    return out


# ══════════════════════════════════════════════════════════════
# VAIIP Risk & Portfolio Engine — new-docs canonical formulas
# (Trade Risk Sheet → Position Ledger → Portfolio Dashboard →
#  PM Decision). All math pure Decimal; risk always positive.
# ══════════════════════════════════════════════════════════════

def _d(x) -> Decimal:
    return D(str(x)) if x is not None else D("0")


# ── Trade Risk Sheet (workbook cols R–Z) ──

def calculate_notional(price, quantity, contract_size=1) -> float:
    """notional = price × qty × contractSize — spec phase 4."""
    return float(_d(price) * _d(quantity) * _d(contract_size))


def calculate_margin(notional, margin_rate) -> float:
    """margin = notional × marginRate — spec phase 4."""
    return float(_d(notional) * _d(margin_rate))


def calculate_risk(entry, stop, quantity, contract_size=1) -> float:
    """risk$ = |entry − stop| × qty × contractSize — always ≥0.
    Corrects the old workbook's negative-risk bug."""
    return float(abs(_d(entry) - _d(stop))
                 * _d(quantity) * _d(contract_size))


def calculate_reward(entry, target, quantity, contract_size=1) -> float:
    """reward$ = |target − entry| × qty × contractSize."""
    return float(abs(_d(target) - _d(entry))
                 * _d(quantity) * _d(contract_size))


def calculate_rr(reward, risk) -> float | None:
    """R/R = reward ÷ risk; None when risk ≤ 0 — never negative."""
    if risk is None or _d(risk) <= 0:
        return None
    return float(_d(reward) / _d(risk))


def calculate_mos(fair_value, entry) -> float | None:
    """MOS = (FV − entry) / FV — positive = below fair value."""
    fv = _d(fair_value)
    if fv <= 0:
        return None
    return float((fv - _d(entry)) / fv)


def calculate_upside(fair_value, current_price) -> float | None:
    """Upside = (FV − current) / current — remaining upside today."""
    p = _d(current_price)
    if p <= 0:
        return None
    return float((_d(fair_value) - p) / p)


def position_size_for_risk(equity, max_risk_pct, entry, stop,
                           contract_size=1, lot_size=1) -> dict:
    """Risk-budget sizing: qty = equity×risk% ÷ (|entry−stop|×CS)."""
    eq, rp, e, s, cs = map(_d, (equity, max_risk_pct, entry, stop,
                               contract_size))
    budget = eq * rp
    per_unit = abs(e - s) * cs
    if per_unit <= 0:
        return {"qty": 0, "risk_budget": float(budget),
                "risk_per_unit": 0.0,
                "note": "invalid stop distance — no size"}
    raw = budget / per_unit
    qty = int(raw / D(str(lot_size))) * lot_size
    return {"qty": float(qty), "qty_raw": float(raw),
            "risk_budget": float(budget),
            "risk_per_unit": float(per_unit)}


def trade_risk_sheet(*, entry, stop, qty, equity, contract_size=1,
                     margin_rate=0, target=None, fair_value=None,
                     spread=0, commission=0, financing=0,
                     open_risk_before=0, current_price=None,
                     direction="long",
                     limits: "Limits | None" = None) -> dict:
    """The full pre-trade scorecard — workbook Trade Risk cols R–AB."""
    L = limits or Limits()
    missing = [k for k, v in (("entry", entry), ("stop", stop),
                              ("qty", qty), ("equity", equity))
               if v is None]
    if missing:
        return {"missing_inputs": missing}
    stop_invalid = ((direction == "long" and _d(stop) >= _d(entry))
                    or (direction == "short"
                        and _d(stop) <= _d(entry)))
    notional = calculate_notional(entry, qty, contract_size)
    margin = calculate_margin(notional, margin_rate)
    risk = calculate_risk(entry, stop, qty, contract_size)
    reward = (calculate_reward(entry, target, qty, contract_size)
              if target is not None else None)
    # net reward after estimated costs (spec §9.F)
    net_reward = (reward - float(_d(spread) * _d(qty))
                  - float(_d(commission)) - float(_d(financing))
                  if reward is not None else None)
    eq = _d(equity)
    lim = L.get("max_portfolio_open_risk_pct")
    capacity = max(0.0, float(eq) * float(lim or 0) - float(
            open_risk_before)) if eq > 0 else 0.0
    px = current_price if current_price is not None else entry
    return {
        "initial_notional": notional,
        "initial_margin": margin,
        "risk_dollars": risk,
        "risk_pct_equity": float(_d(risk) / eq) if eq > 0 else None,
        "reward_dollars": reward,
        "net_reward_dollars": net_reward,
        "rr": calculate_rr(reward, risk) if reward is not None else None,
        "mos_pct": calculate_mos(fair_value, entry)
        if fair_value else None,
        "upside_pct": calculate_upside(fair_value, px)
        if fair_value else None,
        "gross_leverage": float(_d(notional) / eq) if eq > 0 else None,
        "open_risk_before": float(open_risk_before),
        "risk_capacity_after": capacity,
        "stop_invalid": stop_invalid,
    }


def trade_risk_decision(sheet: dict, equity,
                        limits: "Limits | None" = None) -> dict:
    """INPUT / PASS / REVIEW / BLOCK per workbook col AC + spec
    §21/§24/§30:
      INPUT  — required input missing (workbook rule)
      BLOCK  — stop invalid; trade risk > equity×max_trade_risk%;
               trade risk > remaining portfolio risk capacity;
               projected margin utilisation > max
      REVIEW — R/R < min_rr (soft rule)
      PASS   — otherwise"""
    L = limits or Limits()
    eq = _d(equity) or D("1")

    def _out(decision, reason, detail=""):
        return {"decision": decision, "reason": reason,
                "detail": detail, "reasons": [reason]}

    if sheet.get("missing_inputs"):
        missing = ", ".join(sheet["missing_inputs"])
        return _out("INPUT", "required input missing",
                    f"missing: {missing}")
    if sheet.get("stop_invalid"):
        return _out("BLOCK", "stop distance invalid",
                    "stop on the wrong side of entry — hard rule")
    risk = _d(sheet["risk_dollars"])
    max_trade = eq * _d(L.get("max_trade_risk_pct"))
    if risk > max_trade:
        return _out("BLOCK", "trade risk exceeds limit",
                    f"risk ${risk:.0f} > "
                    f"{float(max_trade / eq):.1%} of equity")
    capacity = _d(sheet.get("risk_capacity_after") or 0)
    if risk > capacity:
        return _out("BLOCK", "exceeds portfolio risk capacity",
                    f"risk ${risk:.0f} > remaining capacity "
                    f"${capacity:.0f}")
    mu_lim = L.get("max_margin_utilisation_pct")
    margin = _d(sheet.get("initial_margin") or 0)
    if mu_lim is not None and eq > 0 and margin / eq > _d(mu_lim):
        return _out("BLOCK", "margin utilisation exceeded",
                    f"initial margin {float(margin / eq):.1%} "
                    f"of equity > {mu_lim:.0%}")
    rr = sheet.get("rr")
    if rr is not None and rr < float(L.get("min_rr") or 0):
        return _out("REVIEW", "R/R below minimum",
                    f"{rr:.2f}x < {L.get('min_rr')}x minimum")
    return _out("PASS", "within configured trade-risk rules")


# ── Position Ledger (workbook live-position cols) ──

def position_ledger_row(*, qty, avg_entry, current_price, contract_size=1,
                        margin_rate=0, direction="long",
                        stop=None, target=None, fair_value=None,
                        equity=None, realized_pnl=0, financing=0,
                        commission=0, slippage=0) -> dict:
    """Live position risk accounting — workbook Position Ledger cols.
    Distinguishes initial risk / current open risk / locked-in profit
    (spec §11 — never conflated)."""
    cs = _d(contract_size)
    q, e, c = _d(qty), _d(avg_entry), _d(current_price)
    init_notional = q * e * cs
    cur_notional = q * c * cs
    gross_pnl = ((c - e) if direction == "long" else (e - c)) * q * cs
    init_risk = abs(e - _d(stop)) * q * cs if stop is not None else None
    open_risk = (abs(c - _d(stop)) * q * cs) if stop is not None else None
    locked_in = None
    if stop is not None:
        s = _d(stop)
        if direction == "long" and s > e:
            locked_in = (s - e) * q * cs
        elif direction == "short" and s < e:
            locked_in = (e - s) * q * cs
    remaining = (abs(_d(target) - c) * q * cs
                 if target is not None else None)
    eq = _d(equity) if equity else None
    return {
        "initial_notional": float(init_notional),
        "current_notional": float(cur_notional),
        "initial_margin": float(init_notional * _d(margin_rate)),
        "current_margin": float(cur_notional * _d(margin_rate)),
        "gross_pnl": float(gross_pnl),
        "realized_pnl": float(_d(realized_pnl)),
        "financing": float(_d(financing)),
        "net_pnl": float(gross_pnl + _d(realized_pnl)
                         - _d(financing) - _d(commission)
                         - _d(slippage)),
        "initial_risk": float(init_risk) if init_risk is not None else None,
        "open_risk": float(open_risk) if open_risk is not None else None,
        "unstopped": stop is None,
        "locked_in_profit": (float(locked_in)
                             if locked_in is not None else None),
        "risk_pct_equity": (float(open_risk / eq)
                            if open_risk is not None and eq else None),
        "remaining_reward": (float(remaining)
                             if remaining is not None else None),
        "rr": (calculate_rr(float(remaining), float(open_risk))
               if remaining is not None and open_risk is not None
               else None),
        "mos_pct": (calculate_mos(fair_value, c)
                    if fair_value else None),
    }


# ── Portfolio Dashboard (workbook 8 panels) ──

def portfolio_dashboard(positions: list[dict], equity, cash,
                        limits: "Limits | None" = None) -> dict:
    """Aggregate book view — spec phase 10 + workbook dashboard.
    positions: [{notional, margin, open_risk, net_pnl, remaining_reward,
                 direction, strategy}]"""
    L = limits or Limits()
    eq = _d(equity) or D("1")
    gross = sum(_d(p.get("notional") or 0) for p in positions)
    net = sum((_d(p.get("notional") or 0)
               * (1 if (p.get("direction") or "long") == "long" else -1))
              for p in positions)
    init_margin = sum(_d(p.get("initial_margin")
                          if p.get("initial_margin") is not None
                          else p.get("margin") or 0)
                      for p in positions)
    margin = sum(_d(p.get("margin") or 0) for p in positions)
    maint_margin = sum(_d(p.get("maintenance_margin") or 0)
                       for p in positions)
    # spec Panel 5: Open Stop Risk = losses if all ACTIVE stops are
    # reached. Unstopped positions are NOT in the sum — they are
    # flagged separately (an unstopped position has no defined stop
    # risk to measure, and must not silently cap the risk budget).
    open_stop = sum(_d(p.get("open_risk") or 0) for p in positions
                    if p.get("open_risk") is not None)
    unstopped = sum(_d(p.get("notional") or 0) for p in positions
                    if p.get("open_risk") is None)
    open_risk = open_stop
    net_pnl = sum(_d(p.get("net_pnl") or 0) for p in positions)
    reward = sum(_d(p.get("remaining_reward") or 0) for p in positions)
    by_strategy: dict[str, float] = {}
    by_sector: dict[str, float] = {}
    for p in positions:
        r = p.get("open_risk")
        contrib = _d(r) if r is not None else D("0")
        k = p.get("strategy") or "unassigned"
        by_strategy[k] = float(_d(by_strategy.get(k, 0)) + contrib)
        ks = p.get("sector") or "?"
        by_sector[ks] = float(_d(by_sector.get(ks, 0)) + contrib)
    risk_capacity = max(D("0"), eq * _d(L.get("max_portfolio_open_risk_pct"))
                        - open_risk)
    mu = margin / eq
    gl = gross / eq
    rp = open_risk / eq
    mu_lim = _d(L.get("max_margin_utilisation_pct"))
    # workbook B19: REDUCE when open risk > limit, margin util > limit,
    # or gross leverage > limit
    status = "NORMAL"
    if (rp > _d(L.get("max_portfolio_open_risk_pct"))
            or mu > mu_lim or gl > _d(L.get("max_gross_leverage"))):
        status = "REDUCE"
    elif unstopped > 0:
        status = "WATCH"
    warnings = []
    if unstopped > 0:
        warnings.append(
            f"${float(unstopped):,.0f} notional has no active stop — "
            "open stop risk understates true exposure")
    pm_action = ("Reduce/review positions before adding risk"
                 if status == "REDUCE"
                 else "Set stops on unstopped positions"
                 if status == "WATCH"
                 else "Proceed subject to individual trade controls")
    return {
        "equity": float(eq),
        "gross_notional": float(gross),
        "net_notional": float(net),
        "gross_leverage": float(gl),
        "net_leverage": float(abs(net) / eq),
        "initial_margin": float(init_margin),
        "current_margin": float(margin),
        "maintenance_margin": float(maint_margin),
        "free_margin": float(eq - margin),
        "margin_headroom": float(eq * mu_lim - margin),
        "margin_utilisation": float(mu),
        "open_stop_risk": float(open_stop),
        "unstopped_notional": float(unstopped),
        "open_risk": float(open_risk),
        "open_risk_pct_equity": float(rp),
        "net_pnl": float(net_pnl),
        "remaining_reward": float(reward),
        "portfolio_rr": float(reward / open_risk) if open_risk > 0
        else None,
        "risk_capacity": float(risk_capacity),
        "risk_by_strategy": by_strategy,
        "risk_by_sector": by_sector,
        "status": status,
        "warnings": warnings,
        "pm_action": pm_action,
    }


# ── Maintenance-margin early warning (Exposure doc) ──

def margin_call_check(equity, gross_exposure, *,
                      margin_requirement=0.20,
                      maintenance_margin=None,
                      portfolio_stop_pct=0.20) -> dict:
    """Exposure-doc buffer check: does the portfolio stop trigger
    before a broker margin call?
      margin call when  equity < M% × gross
      equity at stop  = equity × (1 − stop%)
      if equity_at_stop < M%×gross → margin call hits first."""
    eq, ge = _d(equity), _d(gross_exposure)
    mm = _d(maintenance_margin if maintenance_margin is not None
            else margin_requirement)
    threshold = mm * ge
    eq_at_stop = eq * (1 - _d(portfolio_stop_pct))
    # doc: maint ≥ 16% at this config → call before stop; equality
    # means zero buffer — treat as unsafe
    call_first = eq_at_stop <= threshold
    # exposure cap that keeps the stop ahead of the call
    safe_gross = (eq_at_stop / mm) if mm > 0 else None
    return {
        "maintenance_margin_pct": float(mm),
        "margin_call_equity_threshold": float(threshold),
        "equity_at_stop": float(eq_at_stop),
        "margin_call_before_stop": call_first,
        "safe_gross_exposure": float(safe_gross)
        if safe_gross is not None else None,
        "warning": ("margin call may occur BEFORE portfolio stop — "
                    "reduce leverage or raise equity"
                    if call_first else None),
    }


# ── Drawdown escalation ladder (spec §19, workbook B12–14) ──

def drawdown_escalation(dd_pct: float,
                        limits: "Limits | None" = None) -> dict:
    """NORMAL → WATCH → RISK_REDUCTION → TRADING_HALT."""
    L = limits or Limits()
    d = abs(float(dd_pct or 0))
    if d >= float(L.get("halt_drawdown_pct") or 1):
        level = "TRADING_HALT"
        action = "liquidate positions / no new risk — strategy review"
    elif d >= float(L.get("reduce_drawdown_pct") or 1):
        level = "RISK_REDUCTION"
        action = "reduce exposure; no position adds"
    elif d >= float(L.get("warn_drawdown_pct") or 1):
        level = "WATCH"
        action = "heightened monitoring; tighten stops"
    else:
        level = "NORMAL"
        action = "standard controls"
    return {"drawdown": d, "level": level, "action": action,
            "thresholds": {
                "watch": L.get("warn_drawdown_pct"),
                "reduce": L.get("reduce_drawdown_pct"),
                "halt": L.get("halt_drawdown_pct")}}


# ── Named stress scenarios (spec §18/§19, workbook Stress Test) ──

NAMED_SCENARIOS = [
    {"name": "Broad Market Shock", "key": "broad_market",
     "shock": -0.10, "scope": "all"},
    {"name": "Technology Shock", "key": "tech_shock",
     "shock": -0.15, "scope": "tech"},
    {"name": "Semiconductor Shock", "key": "semi_shock",
     "shock": -0.20, "scope": "semis"},
    {"name": "Severe Equity Shock", "key": "severe_equity",
     "shock": -0.20, "scope": "all"},
    {"name": "Volatility Shock", "key": "vol_shock",
     "shock": -0.20, "scope": "all", "beta_mult": 1.5},
    {"name": "Rates Shock (+100bp)", "key": "rates_shock",
     "shock": -0.08, "scope": "growth"},
    {"name": "Liquidity Shock", "key": "liquidity_shock",
     "shock": -0.05, "scope": "all"},
]

_SEMIS = {"NVDA", "AMD", "AVGO", "MRVL", "MU", "TSM", "ASML", "INTC",
          "QCOM", "TXN", "ADI", "ARM", "SMCI", "KLAC", "LRCX"}
_GROWTH = {"Information Technology", "Communication Services"}


def _scope_match(p: dict, scope: str) -> bool:
    if scope == "all":
        return True
    if scope == "tech":
        return "Information Technology" in (p.get("sector") or "")
    if scope == "semis":
        return p.get("symbol") in _SEMIS
    if scope == "growth":
        return (p.get("sector") or "") in _GROWTH
    return False


def named_stress(positions: list[dict], equity,
                 limits: "Limits | None" = None) -> list[dict]:
    """Deterministic scenario P&L — each returns loss, loss/equity,
    and the workbook escalation status (WATCH/REDUCE/HALT)."""
    L = limits or Limits()
    eq = float(equity) or 1.0
    out = []
    for sc in NAMED_SCENARIOS:
        bm = sc.get("beta_mult", 1.0)
        pnl = sum(p["market_value"] * sc["shock"]
                  * p.get("beta", 1.0) * bm
                  for p in positions
                  if _scope_match(p, sc["scope"]))
        loss = abs(pnl)
        loss_pct = loss / eq
        status = ("HALT" if loss_pct > float(
                    L.get("halt_drawdown_pct") or 1)
                  else "REDUCE" if loss_pct > float(
                    L.get("reduce_drawdown_pct") or 1)
                  else "WATCH" if loss_pct > float(
                    L.get("warn_drawdown_pct") or 1)
                  else "OK")
        out.append({"scenario": sc["name"], "key": sc["key"],
                    "shock": sc["shock"], "scope": sc["scope"],
                    "pnl": round(pnl, 2), "loss": round(loss, 2),
                    "loss_pct_equity": round(loss_pct, 4),
                    "status": status})
    return out


# ── PM Decision Engine (spec §20–23, workbook PM Decision) ──

PM_STATES = ("ENTER", "ADD", "HOLD", "REDUCE", "EXIT", "BLOCK",
             "REVIEW")


def pm_decide(*, thesis, valuation, technical, risk_check,
              portfolio_check, has_position=False,
              risk_capacity_ok=True, concentration_high=False,
              target_hit=False, stop_breached=False) -> dict:
    """Rule-based PM decision — deterministic, auditable.

    Inputs (workbook PM Decision row):
      thesis:          VALID | INVALID | REVIEW
      valuation:       ATTRACTIVE | REACHED | REVIEW
      technical:       CONFIRMED | EXIT | WEAKENING | REVIEW
      risk_check:      PASS | REVIEW | BLOCK   (trade risk sheet)
      portfolio_check: PASS | REVIEW | BLOCK   (portfolio gate)

    Workbook base rule:
      BLOCK if risk/portfolio BLOCK;
      EXIT if thesis INVALID or valuation REACHED or technical EXIT;
      ENTER if VALID+ATTRACTIVE+CONFIRMED+PASS+PASS; else REVIEW.
    Spec §25 matrix adds position-aware states (ADD/HOLD/REDUCE).
    """
    # hard rules first — risk veto cannot be overridden.
    # spec §25 matrix: a hard breach is BLOCK *before* the trade;
    # on an existing position the same breach means de-risk → REDUCE.
    if risk_check == "BLOCK" or portfolio_check == "BLOCK":
        if has_position:
            return {"decision": "REDUCE",
                    "reason": "portfolio risk limit breached — "
                              "de-risk the position"}
        return {"decision": "BLOCK",
                "reason": "hard risk/portfolio constraint breached"}
    if stop_breached:
        return {"decision": "EXIT", "reason": "stop breached"}
    if (thesis == "INVALID" or valuation == "REACHED"
            or technical == "EXIT"):
        why = ("thesis invalid" if thesis == "INVALID"
               else "valuation reached fair value"
               if valuation == "REACHED" else "technical exit")
        return {"decision": "EXIT",
                "reason": f"exit condition — {why}"}
    if has_position:
        if portfolio_check == "REVIEW" or concentration_high:
            return {"decision": "REDUCE",
                    "reason": "portfolio risk/concentration elevated"}
        if (thesis == "VALID" and valuation == "ATTRACTIVE"
                and technical == "CONFIRMED" and risk_check == "PASS"
                and risk_capacity_ok and target_hit):
            return {"decision": "ADD",
                    "reason": "thesis valid + target hit + risk "
                              "capacity available"}
        if (thesis == "VALID" and risk_check == "PASS"
                and portfolio_check == "PASS"):
            return {"decision": "HOLD",
                    "reason": "thesis valid, risk within limits"}
        return {"decision": "REVIEW",
                "reason": "pm review required"}
    if (thesis == "VALID" and valuation == "ATTRACTIVE"
            and technical == "CONFIRMED" and risk_check == "PASS"
            and portfolio_check == "PASS"):
        return {"decision": "ENTER",
                "reason": "all gates passed — entry permitted"}
    return {"decision": "REVIEW", "reason": "pm review required"}
