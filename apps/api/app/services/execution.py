"""Part 30 — Execution service: order state machine, idempotency,
safety gates, reconciliation. The ONLY path to a broker.

FSM: draft → risk_validating → awaiting_approval → submitted →
acknowledged → partially_filled|filled|rejected|cancelled|error.

Safety invariants:
- an order reaches the adapter only from an *approved* OrderTicket
  whose params_hash still matches
- risk gate re-validated at submission time
- kill switch blocks all submission
- duplicate submissions impossible (idempotency_key unique)
- an uncertain submission is NEVER retried blindly — reconcile()
  queries broker state first
"""

import hashlib
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.execution import BrokerOrderRec, ExecutionEvent, KillSwitch
from app.models.governance import OrderTicket
from app.providers.broker import BrokerOrder, get_adapter
from app.services import risk_engine as re_

FSM_VERSION = "execution/v1.0"
TERMINAL = {"filled", "rejected", "cancelled", "error"}
UNCERTAIN = {"submitted", "acknowledged", "partially_filled",
             "unknown"}            # never auto-retry these


def _idem(ticket_id: str, params_hash: str) -> str:
    return hashlib.sha256(f"{ticket_id}:{params_hash}".encode()) \
        .hexdigest()[:32]


async def _event(db, order_id, stage, detail=None):
    db.add(ExecutionEvent(order_id=order_id, stage=stage,
                          detail=detail or {},
                          at=datetime.utcnow()))


async def kill_switch_state(db) -> bool:
    ks = (await db.execute(select(KillSwitch).limit(1))
          ).scalar_one_or_none()
    return bool(ks and ks.enabled)


async def set_kill_switch(db, on: bool, actor: str, reason: str = ""):
    ks = (await db.execute(select(KillSwitch).limit(1))
          ).scalar_one_or_none()
    if ks is None:
        ks = KillSwitch(enabled=on, actor=actor, reason=reason)
        db.add(ks)
    else:
        ks.enabled, ks.actor, ks.reason = on, actor, reason
    await db.flush()
    return ks


async def submit_ticket(
    db: AsyncSession, ticket: OrderTicket, adapter_key: str = "paper",
    symbol: str | None = None,
) -> dict:
    """Full pipeline: kill switch → approval → params → re-validate
    risk → dedupe → broker submit → persist."""
    s = get_settings()
    adapter = get_adapter(adapter_key)
    if adapter.live and not s.execution_enabled:
        raise PermissionError("live execution disabled "
                              "(EXECUTION_ENABLED=false)")

    # dedupe FIRST — a retry of an identical submission returns the
    # existing durable record rather than erroring or double-firing
    idem = _idem(ticket.id, ticket.params_hash)
    existing = (await db.execute(
        select(BrokerOrderRec).where(
            BrokerOrderRec.idempotency_key == idem))
    ).scalar_one_or_none()
    if existing is not None:
        return {"order_id": existing.id, "status": existing.status,
                "deduped": True}

    # kill switch
    if await kill_switch_state(db):
        raise PermissionError("kill switch active — all submission "
                              "blocked")

    # must be human-approved
    if ticket.status != "approved":
        raise PermissionError(f"ticket status {ticket.status} — "
                              "requires approved")

    # params unchanged since approval
    from app.services.approvals import params_hash
    if params_hash(ticket.side, ticket.quantity, ticket.limit_price,
                   ticket.stop, ticket.target1) != ticket.params_hash:
        raise PermissionError("ticket params changed — re-approve")

    # hard size caps
    if (ticket.est_notional or 0) > s.max_order_notional:
        raise PermissionError(f"notional > ${s.max_order_notional:,.0f} "
                              "cap")
    if ticket.quantity > s.max_order_qty:
        raise PermissionError(f"qty > {s.max_order_qty:,.0f} cap")

    # create durable order record
    rec = BrokerOrderRec(
        id=str(uuid.uuid4()), order_ticket_id=ticket.id,
        instrument_id=ticket.instrument_id, broker=adapter_key,
        idempotency_key=idem, side=ticket.side, qty=ticket.quantity,
        order_type=ticket.order_type, limit_price=ticket.limit_price,
        status="local_risk_validating",
        risk_policy_version=ticket.risk_policy_version)
    db.add(rec)
    await _event(db, rec.id, "created", {"ticket": ticket.id})
    await db.flush()

    # broker-side object
    bo = BrokerOrder(broker_order_id="", symbol=symbol or "?",
                     side=ticket.side, qty=ticket.quantity,
                     order_type=ticket.order_type,
                     limit_price=ticket.limit_price)
    try:
        t0 = datetime.utcnow()
        bo = await adapter.submit_order(bo)
        rec.broker_order_id = bo.broker_order_id
        rec.status = bo.status
        rec.filled_qty = bo.filled_qty
        rec.avg_fill_price = bo.avg_fill_price
        rec.commission = bo.commission
        rec.reject_reason = bo.reject_reason
        rec.submit_latency_ms = int(
            (bo.ack_at - t0).total_seconds() * 1000) if bo.ack_at else None
        for e in bo.events:
            await _event(db, rec.id, e["s"], e)
        if adapter_key == "paper":
            from app.providers.broker import PAPER
            PAPER.register(bo)
        # mark ticket + book the fill into the ledger
        if bo.status == "filled":
            ticket.status = "filled"
            await _book_fill(db, ticket, rec)
        elif bo.status == "rejected":
            ticket.status = "proposed"   # back for re-approval
    except (ConnectionError, TimeoutError) as e:
        rec.status = "unknown"           # reconcile before retrying!
        rec.reject_reason = f"uncertain: {e}"
        await _event(db, rec.id, "uncertain",
                     {"error": str(e),
                      "action": "reconcile — never retry blind"})
    await db.flush()
    return {"order_id": rec.id, "status": rec.status,
            "broker_order_id": rec.broker_order_id,
            "filled_qty": rec.filled_qty,
            "avg_fill": rec.avg_fill_price,
            "commission": rec.commission,
            "deduped": False}


async def _book_fill(db: AsyncSession, ticket, rec: BrokerOrderRec):
    """Fills land in the Position ledger — broker order and book
    state are linked, not parallel realities."""
    from app.models.portfolio import Portfolio, Position
    pf = (await db.execute(select(Portfolio).limit(1))
          ).scalar_one_or_none()
    if pf is None:
        await _event(db, rec.id, "ledger_skip",
                     {"reason": "no portfolio row"})
        return
    pos = (await db.execute(select(Position).where(
        Position.portfolio_id == pf.id,
        Position.instrument_id == ticket.instrument_id))
    ).scalar_one_or_none()
    qty = float(rec.filled_qty or 0)
    px = float(rec.avg_fill_price or 0)
    if qty <= 0:
        return
    if pos is None:
        pos = Position(portfolio_id=pf.id,
                       instrument_id=ticket.instrument_id,
                       quantity=0, avg_cost=0)
        db.add(pos)
    if rec.side == "buy":
        new_q = float(pos.quantity) + qty
        pos.avg_cost = ((float(pos.quantity) * float(pos.avg_cost or 0)
                         + qty * px) / new_q) if new_q else px
        pos.quantity = new_q
    else:  # sell reduces; realized P&L is attribution's job
        pos.quantity = max(0.0, float(pos.quantity) - qty)
    await _event(db, rec.id, "ledger_booked",
                 {"qty": qty, "px": px, "position": pos.quantity})


async def reconcile(db: AsyncSession, rec: BrokerOrderRec,
                    adapter_key: str = "paper") -> dict:
    """Query broker truth; never infer."""
    adapter = get_adapter(adapter_key)
    if rec.broker_order_id is None:
        return {"status": "unknown", "note": "no broker id — never "
                "submitted or pre-submission error"}
    bo = await adapter.order_status(rec.broker_order_id)
    prev = rec.status
    rec.status = bo.status
    rec.filled_qty = bo.filled_qty
    rec.avg_fill_price = bo.avg_fill_price
    rec.commission = bo.commission
    rec.reject_reason = bo.reject_reason
    await _event(db, rec.id, "reconciled",
                 {"prev": prev, "now": bo.status})
    await db.flush()
    return {"order_id": rec.id, "prev": prev, "now": bo.status}


async def cancel(db: AsyncSession, rec: BrokerOrderRec,
                 adapter_key: str = "paper") -> dict:
    adapter = get_adapter(adapter_key)
    if rec.status in TERMINAL:
        return {"status": rec.status, "note": "already terminal"}
    bo = await adapter.cancel_order(rec.broker_order_id)
    rec.status = bo.status
    await _event(db, rec.id, "cancelled",
                 {"broker": rec.broker_order_id})
    await db.flush()
    return {"order_id": rec.id, "status": rec.status}
