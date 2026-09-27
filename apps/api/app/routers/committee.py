from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.agents import AgentOutput, AgentRun
from app.models.governance import Approval, DecisionRecord
from app.models.identity import User
from app.models.instruments import Instrument
from app.security import audit, require
from app.services import committee as cs

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
