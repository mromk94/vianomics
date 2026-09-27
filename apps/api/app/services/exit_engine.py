"""Part 24 — Exit engine: six exit classes + pyramid exits.

Exit lifecycle — each stage is distinct and recorded:
  signal → triggered → proposed order → submitted → broker fill.
A position is NEVER 'closed' because a signal fired — only on a
confirmed fill (or broker reconciliation) is it closed.
"""

from dataclasses import dataclass
from datetime import datetime

EXIT_VERSION = "exit-engine/v1.0"

# exit classes
FUNDAMENTAL = "fundamental"   # deterioration / thesis broken
VALUATION = "valuation"       # price ≥ IV
TECHNICAL = "technical"       # stop / invalidation
RISK = "risk"                 # limit breach, drawdown
MARKET = "market"             # macro/regime turn
THESIS = "thesis"             # thesis no longer holds

# lifecycle
SIGNAL = "signal"             # condition identified (recommendation)
TRIGGERED = "triggered"       # numeric condition met
PROPOSED = "proposed_order"   # exit order constructed
SUBMITTED = "submitted"       # sent to broker (paper or real)
FILLED = "filled"             # broker-confirmed fill
CLOSED = "closed"             # position fully flat post-fill


def exit_signals(position: dict, ctx: dict) -> list[dict]:
    """Evaluate all six classes for an open position."""
    sigs = []
    price = ctx.get("price")
    iv = (ctx.get("valuation_outputs") or {}).get("dcf", {}) \
        .get("per_share")
    stop = position.get("stop")
    macro = ctx.get("macro") or {}

    # 1. fundamental
    if position.get("fundamentals_deteriorated"):
        sigs.append({"cls": FUNDAMENTAL, "stage": SIGNAL,
                     "action": "exit_all",
                     "reason": "fundamental deterioration"})
    # 2. valuation
    if iv and price and price >= iv:
        sigs.append({"cls": VALUATION, "stage": SIGNAL,
                     "action": "reduce_or_exit",
                     "reason": f"price {price} ≥ IV {iv}"})
    elif iv and price and price >= iv * 0.9:
        sigs.append({"cls": VALUATION, "stage": SIGNAL,
                     "action": "reduce",
                     "reason": "within 10% of IV"})
    # 3. technical (stop)
    if stop and price and price < stop:
        sigs.append({"cls": TECHNICAL, "stage": TRIGGERED,
                     "action": "exit_all",
                     "reason": f"price {price} < stop {stop}"})
    # 4. risk
    for b in ctx.get("risk_gate", {}).get("breaches", []):
        if b.get("blocking", True):
            sigs.append({"cls": RISK, "stage": SIGNAL,
                         "action": "reduce",
                         "reason": f"limit breach: {b['rule']}"})
    # 5. market
    if macro.get("market_regime") == "risk_off":
        sigs.append({"cls": MARKET, "stage": SIGNAL,
                     "action": "reduce",
                     "reason": "market regime risk_off"})
    # 6. thesis
    if position.get("thesis_broken"):
        sigs.append({"cls": THESIS, "stage": TRIGGERED,
                     "action": "exit_all",
                     "reason": "thesis invalidation"})
    return sigs


def pyramid_exit(trade, price: float, fill_price: float | None = None):
    """Pyramid-specific exits:
    stop hit → exit ALL at observed fill;
    T1 → double (handled by risk_engine.advance — never exit);
    T2 → per t2_policy (trail | profit_50).
    """
    from app.services import risk_engine as re_
    return re_.advance(trade, price, trade.atr_initial,
                       fill_price=fill_price)


def exit_order(proposed: dict) -> dict:
    """Construct the proposed exit order — still not submitted."""
    return {
        "stage": PROPOSED,
        "version": EXIT_VERSION,
        "symbol": proposed["symbol"],
        "side": "sell",
        "qty": proposed["qty"],
        "order_type": proposed.get("order_type", "market"),
        "limit_price": proposed.get("limit_price"),
        "reason": proposed.get("reason"),
        "requires_approval": True,
        "created_at": datetime.utcnow().isoformat(),
    }
