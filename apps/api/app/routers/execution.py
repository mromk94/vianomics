"""Execution API — paper-lane order flow + monitoring.

Every order reaches the broker ONLY through submit_ticket() which
enforces: approved ticket → params_hash → risk revalidation →
idempotency → kill switch. No route bypasses this."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.execution import BrokerOrderRec, ExecutionEvent, KillSwitch
from app.models.governance import OrderTicket
from app.models.identity import User
from app.models.instruments import Instrument
from app.providers.broker import get_adapter, ProviderConfigError
from app.security import audit, require
from app.services import execution as ex

router = APIRouter(prefix="/execution", tags=["execution"])


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db)):
    from app.config import get_settings
    s = get_settings()
    ks = await ex.kill_switch_state(db)
    adapters = {}
    for key in ("paper", "ibkr", "alpaca"):
        try:
            a = get_adapter(key)
            adapters[key] = {"operational": True,
                             **a.capabilities()}
        except Exception as e:
            adapters[key] = {"operational": False, "reason": str(e)[:120]}
    return {
        "execution_enabled": s.execution_enabled,
        "execution_broker": s.execution_broker,
        "kill_switch": ks,
        "live_note": "live trading requires EXECUTION_ENABLED=true + "
                     "configured IBKR + verified permissions — "
                     "disabled by default",
        "adapters": adapters,
        "fsm_version": ex.FSM_VERSION,
    }


@router.get("/account/{broker}")
async def account(broker: str, db: AsyncSession = Depends(get_db)):
    try:
        a = get_adapter(broker)
    except ProviderConfigError as e:
        raise HTTPException(400, str(e))
    return {"broker": broker, "summary": await a.account_summary(),
            "positions": await a.positions()}


@router.post("/orders/{ticket_id}/submit", status_code=201)
async def submit(
    ticket_id: str, broker: str = "paper",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("trading:execute")),
):
    t = await db.get(OrderTicket, ticket_id)
    if t is None:
        raise HTTPException(404, "ticket not found")
    inst = await db.get(Instrument, t.instrument_id)
    # paper lane: feed the delayed-EOD close as the simulated quote
    if broker == "paper":
        from app.models.market import OhlcvBar
        from app.providers.broker import PAPER
        bar = (
            await db.execute(
                select(OhlcvBar)
                .where(OhlcvBar.instrument_id == inst.id,
                       OhlcvBar.timeframe == "1d")
                .order_by(OhlcvBar.time.desc()).limit(1))
        ).scalar_one_or_none()
        if bar:
            PAPER.set_quote(inst.symbol, float(bar.close))
    try:
        res = await ex.submit_ticket(db, t, adapter_key=broker,
                                     symbol=inst.symbol)
    except (PermissionError, ValueError) as e:
        raise HTTPException(403, str(e))
    await audit(db, action="execution.submit", actor=user,
                entity_type="broker_order", entity_id=res["order_id"],
                detail=res)
    await db.commit()
    return res


@router.post("/orders/{order_id}/cancel")
async def cancel_order(
    order_id: str, broker: str = "paper",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("trading:execute")),
):
    rec = await db.get(BrokerOrderRec, order_id)
    if rec is None:
        raise HTTPException(404, "order not found")
    res = await ex.cancel(db, rec, adapter_key=broker)
    await audit(db, action="execution.cancel", actor=user,
                entity_type="broker_order", entity_id=order_id)
    await db.commit()
    return res


@router.post("/orders/{order_id}/reconcile")
async def reconcile(
    order_id: str, broker: str = "paper",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("trading:execute")),
):
    rec = await db.get(BrokerOrderRec, order_id)
    if rec is None:
        raise HTTPException(404, "order not found")
    res = await ex.reconcile(db, rec, adapter_key=broker)
    await db.commit()
    return res


@router.get("/orders")
async def orders(limit: int = 50, db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(BrokerOrderRec, Instrument.symbol)
            .join(Instrument,
                  BrokerOrderRec.instrument_id == Instrument.id)
            .order_by(BrokerOrderRec.created_at.desc()).limit(limit))
    ).all()
    return [
        {"id": r.id, "symbol": s, "broker": r.broker, "side": r.side,
         "qty": r.qty, "status": r.status, "filled_qty": r.filled_qty,
         "avg_fill": r.avg_fill_price, "commission": r.commission,
         "reject": r.reject_reason,
         "submit_latency_ms": r.submit_latency_ms,
         "idempotency": r.idempotency_key[:8],
         "created_at": r.created_at.isoformat()}
        for r, s in rows
    ]


@router.get("/orders/{order_id}/events")
async def events(order_id: str, db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(ExecutionEvent)
            .where(ExecutionEvent.order_id == order_id)
            .order_by(ExecutionEvent.at))
    ).scalars().all()
    return [{"stage": e.stage, "detail": e.detail,
             "at": e.at.isoformat()} for e in rows]


@router.post("/kill-switch")
async def kill_switch(
    enabled: bool, reason: str = "",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("portfolio:approve")),
):
    ks = await ex.set_kill_switch(db, enabled, user.id, reason)
    await audit(db,
                action="execution.kill_switch_on" if enabled else
                "execution.kill_switch_off", actor=user,
                entity_type="kill_switch", entity_id=ks.id,
                detail={"reason": reason})
    await db.commit()
    return {"enabled": ks.enabled, "reason": ks.reason,
            "note": "submissions blocked; cancellations still flow"}
