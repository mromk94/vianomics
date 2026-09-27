from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.agents import AgentOutput, AgentRun
from app.models.governance import (
    Approval, DecisionRecord, OrderTicket,
)
from app.models.identity import User
from app.models.instruments import Instrument, Sector
from app.models.market import OhlcvBar
from app.security import audit, require
from app.services import approvals as appr_svc
from app.services import committee as cs
from app.routers.risk import _portfolio_ctx

router = APIRouter(prefix="/committee", tags=["committee"])


@router.post("/evaluate/{symbol}", status_code=201)
async def evaluate(
    symbol: str, growth: float = 0.15,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("research:run")),
):
    inst = (
        await db.execute(
            select(Instrument).where(Instrument.symbol == symbol.upper()))
    ).scalar_one_or_none()
    if inst is None:
        raise HTTPException(404, f"{symbol} not in security master")
    result = await cs.run_committee(db, inst, growth=growth,
                                    actor_id=user.id)
    await audit(db, action="committee.evaluate", actor=user,
                entity_type="decision_record",
                entity_id=result["decision_id"],
                detail={"verdict": result["verdict"]})
    await db.commit()
    return result


@router.get("/decisions")
async def decisions(limit: int = 25, db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(DecisionRecord, Instrument.symbol)
            .join(Instrument,
                  DecisionRecord.instrument_id == Instrument.id)
            .order_by(DecisionRecord.at.desc()).limit(limit))
    ).all()
    return [
        {"id": d.id, "symbol": s, "at": d.at.isoformat(),
         "verdict": d.verdict, "confidence": d.cio_confidence,
         "numbers": d.numbers, "mandate_version": d.mandate_version}
        for d, s in rows
    ]


@router.get("/decisions/{decision_id}")
async def decision(decision_id: str, db: AsyncSession = Depends(get_db)):
    d = await db.get(DecisionRecord, decision_id)
    if d is None:
        raise HTTPException(404, "decision not found")
    runs = (
        await db.execute(
            select(AgentRun, AgentOutput)
            .join(AgentOutput, AgentOutput.run_id == AgentRun.id)
            .where(AgentRun.instrument_id == d.instrument_id)
            .order_by(AgentRun.created_at.desc())
            .limit(10))
    ).all()
    appr = (
        await db.execute(
            select(Approval).where(Approval.decision_id == d.id))
    ).scalar_one_or_none()
    sym = (
        await db.execute(
            select(Instrument.symbol).where(Instrument.id == d.instrument_id))
    ).scalar()
    return {
        "id": d.id, "symbol": sym, "at": d.at.isoformat(),
        "verdict": d.verdict, "confidence": d.cio_confidence,
        "gate_results": d.gate_results, "agent_scores": d.agent_scores,
        "numbers": d.numbers, "mandate_version": d.mandate_version,
        "approval": ({"id": appr.id, "status": appr.status,
                      "reason": appr.reason} if appr else None),
        "agents": [
            {"agent": r.agent_key, "status": r.status,
             "report": o.payload}
            for r, o in runs],
    }


@router.post("/decisions/{decision_id}/review")
async def review(
    decision_id: str, action: str,  # approve|reject
    reason: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("portfolio:approve")),
):
    if action not in ("approve", "reject"):
        raise HTTPException(400, "action must be approve|reject")
    appr = (
        await db.execute(
            select(Approval).where(
                Approval.decision_id == decision_id,
                Approval.status == "pending"))
    ).scalar_one_or_none()
    if appr is None:
        raise HTTPException(404, "no pending approval for decision")
    appr.status = "approved" if action == "approve" else "rejected"
    appr.approved_by = user.id
    appr.reason = reason
    from app.db.base import utcnow
    appr.decided_at = utcnow()
    # verdict reflects human outcome — no_trade if rejected
    d = await db.get(DecisionRecord, decision_id)
    if action == "reject" and d:
        d.verdict = "rejected_by_human"
    await audit(db, action=f"decision.{action}", actor=user,
                entity_type="approval", entity_id=appr.id)
    await db.commit()
    return {"decision_id": decision_id, "approval": appr.status}


# ── Part 23: order tickets ──

class ProposeIn(BaseModel):
    side: str = "buy"
    quantity: float = Field(gt=0)
    limit_price: float | None = None
    stop: float | None = None
    target1: float | None = None
    target2: float | None = None


@router.post("/decisions/{decision_id}/orders", status_code=201)
async def propose_order(
    decision_id: str, body: ProposeIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("trading:execute")),
):
    """Propose an order from a decision — requires the CIO verdict to
    be approve_pending_human and the decision not already rejected."""
    d = await db.get(DecisionRecord, decision_id)
    if d is None:
        raise HTTPException(404, "decision not found")
    if d.verdict != "approve_pending_human":
        raise HTTPException(
            409, f"decision verdict '{d.verdict}' — orders only from "
                 "approve_pending_human")
    inst = await db.get(Instrument, d.instrument_id)
    bar = (
        await db.execute(
            select(OhlcvBar)
            .where(OhlcvBar.instrument_id == inst.id,
                   OhlcvBar.timeframe == "1d")
            .order_by(OhlcvBar.time.desc()).limit(1))
    ).scalar_one_or_none()
    if bar is None:
        raise HTTPException(400, "no price data — cannot propose")
    price = float(bar.close)
    pf = await _portfolio_ctx(db)
    t = await appr_svc.propose_order(
        db, d, symbol=inst.symbol, side=body.side, qty=body.quantity,
        price=price, limit_price=body.limit_price, stop=body.stop,
        target1=body.target1, target2=body.target2, portfolio_ctx=pf,
        cio_report={"verdict": d.verdict, "confidence": d.cio_confidence},
        data_freshness={"last_bar": bar.time.isoformat(),
                        "delayed": True})
    await audit(db, action="order.propose", actor=user,
                entity_type="order_ticket", entity_id=t.id)
    await db.commit()
    return {"id": t.id, "status": t.status,
            "est_notional": t.est_notional, "reward_risk": t.reward_risk,
            "exposure_after": t.exposure_after,
            "params_hash": t.params_hash,
            "risk_policy_version": t.risk_policy_version}


@router.post("/orders/{ticket_id}/approve")
async def approve_order(
    ticket_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("portfolio:approve")),
):
    """Human approval — verifies params unchanged AND risk conditions
    unchanged since proposal; refuses (stale) otherwise."""
    t = await db.get(OrderTicket, ticket_id)
    if t is None:
        raise HTTPException(404, "ticket not found")
    if t.status != "proposed":
        raise HTTPException(409, f"ticket already {t.status}")
    inst = await db.get(Instrument, t.instrument_id)
    sec = (await db.execute(
        select(Sector).where(Sector.id == inst.sector_id))
    ).scalar_one_or_none() if inst.sector_id else None
    pf = await _portfolio_ctx(db)
    try:
        t = await appr_svc.approve_order(
            db, t, user.id, pf_ctx=pf,
            sector=sec.name if sec else None)
    except ValueError as e:
        await db.commit()
        raise HTTPException(409, str(e))
    await audit(db, action="order.approve", actor=user,
                entity_type="order_ticket", entity_id=t.id)
    await db.commit()
    return {"id": t.id, "status": t.status,
            "approved_at": t.approved_at.isoformat(),
            "note": "approval ≠ execution — submission is a separate, "
                    "authorized service call"}


@router.post("/orders/{ticket_id}/reject")
async def reject_order(
    ticket_id: str, reason: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("portfolio:approve")),
):
    t = await db.get(OrderTicket, ticket_id)
    if t is None:
        raise HTTPException(404, "ticket not found")
    t.status = "rejected"
    await audit(db, action="order.reject", actor=user,
                entity_type="order_ticket", entity_id=t.id,
                detail={"reason": reason})
    await db.commit()
    return {"id": t.id, "status": "rejected"}


@router.get("/orders")
async def orders(limit: int = 50, db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(OrderTicket, Instrument.symbol)
            .join(Instrument, OrderTicket.instrument_id == Instrument.id)
            .order_by(OrderTicket.created_at.desc()).limit(limit))
    ).all()
    return [
        {"id": t.id, "symbol": s, "side": t.side, "qty": t.quantity,
         "limit": t.limit_price, "notional": t.est_notional,
         "status": t.status, "rr": t.reward_risk,
         "approved_at": t.approved_at.isoformat() if t.approved_at
         else None,
         "params_hash": t.params_hash[:8]}
        for t, s in rows
    ]


# ── Part E: decision replay ──

@router.get("/decisions/{decision_id}/replay")
async def replay(decision_id: str, db: AsyncSession = Depends(get_db)):
    """Reconstruct a historical decision entirely from stored
    snapshots — no live market queries, no model reruns."""
    d = await db.get(DecisionRecord, decision_id)
    if d is None:
        raise HTTPException(404, "decision not found")
    sym = (await db.execute(
        select(Instrument.symbol).where(Instrument.id == d.instrument_id))
    ).scalar()
    runs = (
        await db.execute(
            select(AgentRun, AgentOutput)
            .join(AgentOutput, AgentOutput.run_id == AgentRun.id)
            .where(AgentRun.instrument_id == d.instrument_id)
            .order_by(AgentRun.created_at.desc()).limit(10))
    ).all()
    appr = (
        await db.execute(
            select(Approval).where(Approval.decision_id == d.id))
    ).scalar_one_or_none()
    tickets = (
        await db.execute(
            select(OrderTicket).where(OrderTicket.decision_id == d.id))
    ).scalars().all()
    return {
        "replayed_from": "stored snapshots only — no live queries",
        "decision": {
            "id": d.id, "symbol": sym, "at": d.at.isoformat(),
            "verdict": d.verdict, "confidence": d.cio_confidence,
            "mandate_version": d.mandate_version,
            "gate_tree": d.gate_results.get("tree"),
            "entry_protocol": d.gate_results.get("entry_protocol"),
            "numbers": d.numbers, "agent_scores": d.agent_scores,
        },
        "agent_outputs": [
            {"agent": r.agent_key, "status": r.status,
             "prompt_version": r.prompt_hash,
             "report": o.payload} for r, o in runs],
        "approval": ({"status": appr.status, "by": appr.approved_by,
                      "at": appr.decided_at.isoformat()
                      if appr.decided_at else None}
                     if appr else None),
        "order_tickets": [
            {"id": t.id, "side": t.side, "qty": t.quantity,
             "limit": t.limit_price, "status": t.status,
             "risk_policy": t.risk_policy_version,
             "approved_at": t.approved_at.isoformat()
             if t.approved_at else None}
            for t in tickets],
    }
