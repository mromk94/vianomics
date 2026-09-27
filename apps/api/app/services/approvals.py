"""Part 23 — order tickets + human approval with staleness checks.

Approval binds to a params_hash of {side, qty, limit_price, stop,
target1} and a risk_snapshot. approve() re-runs the risk gate; if
order params changed or new blocking breaches appear, the approval
is refused and the ticket returns to 'proposed' — renewed approval
required. CIO recommendation is NOT authorization.
"""

import hashlib
import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.models.governance import DecisionRecord, OrderTicket
from app.services import risk_engine as re_

RISK_POLICY = re_.ENGINE_VERSION


def params_hash(side, qty, limit_price, stop, target1) -> str:
    canonical = json.dumps(
        {"side": side, "qty": round(float(qty), 6),
         "limit_price": limit_price, "stop": stop,
         "target1": target1},
        sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def propose_order(
    db: AsyncSession, decision: DecisionRecord, *, symbol, side, qty,
    price, limit_price=None, stop=None, target1=None, target2=None,
    portfolio_ctx=None, fees=None, cio_report=None,
    data_freshness=None,
) -> OrderTicket:
    """Create a proposed order — stage 'proposed', not submitted."""
    pf = portfolio_ctx or {}
    nav = pf.get("nav") or 1
    notional = qty * (limit_price or price)
    exp_before = sum(p.get("market_value", 0) for p in
                     pf.get("positions", []))
    rr = (None if not stop or not target1 else
          round((target1 - price) / max(1e-9, price - stop), 2))

    gate = re_.check_order(
        {"symbol": symbol, "side": side,
         "sector": decision.gate_results.get("sector"),
         "notional": notional}, pf)

    t = OrderTicket(
        decision_id=decision.id,
        instrument_id=decision.instrument_id,
        side=side, quantity=qty, limit_price=limit_price,
        est_notional=notional, est_fees=fees,
        stop=stop, target1=target1, target2=target2,
        reward_risk=rr,
        status="proposed",
        params_hash=params_hash(side, qty, limit_price, stop, target1),
        risk_snapshot={"gate": gate, "nav": nav,
                       "as_of": utcnow().isoformat()},
        exposure_before={"gross_value": exp_before,
                         "nav": nav},
        exposure_after={"gross_value":
                        exp_before + (notional if side == "buy" else 0),
                        "nav": nav,
                        "new_position_pct": round(notional / nav, 4)},
        cio_report=cio_report or {},
        data_freshness=data_freshness or {},
        risk_policy_version=RISK_POLICY,
    )
    db.add(t)
    await db.flush()
    return t


async def approve_order(
    db: AsyncSession, ticket: OrderTicket, user_id: str, *,
    pf_ctx: dict, sector: str | None = None,
) -> OrderTicket:
    """Approve only if params unchanged AND risk gate still passes."""
    # params integrity — any change since proposal → stale approval
    current = params_hash(ticket.side, ticket.quantity,
                          ticket.limit_price, ticket.stop, ticket.target1)
    if current != ticket.params_hash:
        ticket.status = "proposed"
        ticket.risk_snapshot = {**ticket.risk_snapshot,
                                "renewal_reason": "params changed"}
        await db.flush()
        raise ValueError("order parameters changed — renewed approval "
                         "required")

    # re-run risk gate against CURRENT portfolio — conditions may have
    # changed since proposal
    gate = re_.check_order(
        {"symbol": "?", "side": ticket.side, "sector": sector,
         "notional": ticket.est_notional or 0}, pf_ctx)
    new_blocks = [b["rule"] for b in gate["breaches"]
                  if b.get("blocking", True)]
    old_blocks = [b["rule"] for b in
                  ticket.risk_snapshot.get("gate", {})
                  .get("breaches", []) if b.get("blocking", True)]
    if set(new_blocks) - set(old_blocks):
        ticket.status = "proposed"
        ticket.risk_snapshot = {**ticket.risk_snapshot,
                                "renewal_reason": "risk conditions "
                                "changed", "new_blocks": new_blocks}
        await db.flush()
        raise ValueError(
            f"risk conditions changed ({', '.join(new_blocks)}) — "
            "renewed approval required")

    ticket.status = "approved"
    ticket.approved_by = user_id
    ticket.approved_at = utcnow()
    await db.flush()
    return ticket
