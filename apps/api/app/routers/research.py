from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.dossier import (
    Dossier,
    DossierReview,
    DossierSection,
    EvidenceItem,
)
from app.models.identity import User
from app.models.instruments import Instrument
from app.models.screening import ScreeningPolicy
from app.security import audit, require
from app.services import mandate as mandate_svc
from app.services import research_orchestrator as ro

router = APIRouter(prefix="/research", tags=["research"])


async def _inst(db: AsyncSession, symbol: str) -> Instrument:
    inst = (
        await db.execute(
            select(Instrument).where(Instrument.symbol == symbol.upper())
        )
    ).scalar_one_or_none()
    if inst is None:
        raise HTTPException(404, f"{symbol} not in security master")
    return inst


def _dossier_dict(d: Dossier, sections: list[DossierSection]) -> dict:
    return {
        "id": d.id, "group_id": d.group_id, "version": d.version,
        "status": d.status,
        "mandate_version": d.mandate_version,
        "policy_version": d.policy_version,
        "green_zone_score": d.green_zone_score,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "missing_evidence": d.missing_evidence,
        "workflow_log": d.workflow_log,
        "sections": [
            {"section_no": s.section_no, "title": s.title,
             "status": s.status, "content": s.content}
            for s in sections
        ],
    }


@router.post("/dossier/{symbol}", status_code=201)
async def generate(
    symbol: str,
    price: float | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("research:run")),
) -> dict:
    inst = await _inst(db, symbol)
    mandate = await mandate_svc.get_active(db)
    pol = (
        await db.execute(
            select(ScreeningPolicy).where(ScreeningPolicy.is_active.is_(True))
        )
    ).scalars().first()
    d = await ro.build_dossier(
        db, inst, user.id, mandate,
        pol.version if pol else None, price=price,
    )
    await audit(db, action="research.dossier", actor=user,
                entity_type="dossier", entity_id=d.id,
                detail={"symbol": inst.symbol, "version": d.version})
    await db.commit()
    return {"id": d.id, "version": d.version, "status": d.status,
            "missing_evidence": d.missing_evidence}


@router.get("/dossier/{symbol}")
async def latest(symbol: str, db: AsyncSession = Depends(get_db)) -> dict:
    inst = await _inst(db, symbol)
    d = (
        await db.execute(
            select(Dossier)
            .where(Dossier.instrument_id == inst.id)
            .order_by(Dossier.created_at.desc())
        )
    ).scalars().first()
    if d is None:
        raise HTTPException(404, f"no dossier for {symbol}")
    sections = (
        await db.execute(
            select(DossierSection)
            .where(DossierSection.dossier_id == d.id)
            .order_by(DossierSection.section_no)
        )
    ).scalars().all()
    ev = (
        await db.execute(
            select(EvidenceItem).where(EvidenceItem.instrument_id == inst.id)
        )
    ).scalars().all()
    out = _dossier_dict(d, sections)
    out["symbol"] = inst.symbol
    out["name"] = inst.name
    out["evidence"] = {
        e.id: {"kind": e.kind, "concept": e.concept, "value": e.value,
               "unit": e.unit, "period_end": e.period_end,
               "source": e.source}
        for e in ev
    }
    return out


@router.get("/dossier/{symbol}/versions")
async def versions(symbol: str, db: AsyncSession = Depends(get_db)) -> list[dict]:
    inst = await _inst(db, symbol)
    rows = (
        await db.execute(
            select(Dossier)
            .where(Dossier.instrument_id == inst.id)
            .order_by(Dossier.version.desc())
        )
    ).scalars().all()
    return [
        {"id": d.id, "version": d.version, "status": d.status,
         "created_at": d.created_at.isoformat() if d.created_at else None,
         "green_zone_score": d.green_zone_score}
        for d in rows
    ]


class ReviewIn(BaseModel):
    verdict: str  # approve|request_changes|attest
    note: str | None = None


@router.post("/dossier/{symbol}/review", status_code=201)
async def review(
    symbol: str,
    body: ReviewIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("research:write")),
) -> dict:
    if body.verdict not in ("approve", "request_changes", "attest"):
        raise HTTPException(422, "verdict must be approve|request_changes|attest")
    inst = await _inst(db, symbol)
    d = (
        await db.execute(
            select(Dossier)
            .where(Dossier.instrument_id == inst.id)
            .order_by(Dossier.created_at.desc())
        )
    ).scalars().first()
    if d is None:
        raise HTTPException(404, "no dossier")
    r = DossierReview(
        dossier_id=d.id, reviewer_id=user.id,
        verdict=body.verdict, note=body.note,
    )
    db.add(r)
    if body.verdict == "approve":
        d.status = "final"
    elif body.verdict == "request_changes":
        d.status = "review"
    await audit(db, action="research.review", actor=user,
                entity_type="dossier", entity_id=d.id,
                detail={"verdict": body.verdict})
    await db.commit()
    return {"dossier": d.id, "status": d.status, "verdict": body.verdict}


@router.get("/valuation/{symbol}")
async def valuation(symbol: str, price: float | None = Query(None),
                    db: AsyncSession = Depends(get_db)) -> dict:
    from app.services.fundamentals_query import fy_series, last_two, latest_instant
    from app.services.green_zone import (
        C_CASH, C_DA, C_DEBT, C_EBIT, C_EQUITY, C_NI, C_OCF, C_CAPEXC, C_SHARES,
    )
    from app.services import finmetrics as fm
    from app.services import valuation as val
    from app.db.base import utcnow

    inst = await _inst(db, symbol)
    as_of = utcnow()
    sector = None
    if inst.sector_id:
        from app.models.instruments import Sector
        sector = (
            await db.execute(select(Sector).where(Sector.id == inst.sector_id))
        ).scalar_one_or_none()
    sector_name = sector.name if sector else None

    ni = await fy_series(db, inst.id, C_NI, as_of)
    ebit = await fy_series(db, inst.id, C_EBIT, as_of)
    ocf = await fy_series(db, inst.id, C_OCF, as_of)
    capex = await fy_series(db, inst.id, C_CAPEXC, as_of)
    da = await fy_series(db, inst.id, C_DA, as_of)
    shares_s = await fy_series(db, inst.id, C_SHARES, as_of)
    equity = await latest_instant(db, inst.id, C_EQUITY, as_of)
    debt = await latest_instant(db, inst.id, C_DEBT, as_of)
    cash = await latest_instant(db, inst.id, C_CASH, as_of)

    sh = last_two(shares_s)[1]
    eps = fm.per_share(last_two(ni)[1], sh)
    fcf_ps = fm.per_share(fm.fcf(last_two(ocf)[1], last_two(capex)[1]), sh)
    ebitda = ((last_two(ebit)[1] or 0) + (last_two(da)[1] or 0)) \
        if last_two(ebit)[1] is not None else None
    from decimal import Decimal
    net_debt = Decimal(str(debt or 0)) - Decimal(str(cash or 0))

    out = val.compute(
        sector_name, price=price, shares=sh, equity=equity,
        eps=eps, ebitda=ebitda, net_debt=net_debt,
        ni_series=[v for _, v in sorted(ni.items())], fcf_ps=fcf_ps,
    )
    out["symbol"] = inst.symbol
    out["sector"] = sector_name
    out["allowed_metrics"] = sorted(val.allowed_metrics(sector_name))
    out["price"] = price
    return out


@router.get("/qualification/{symbol}")
async def qualification(symbol: str, db: AsyncSession = Depends(get_db)
                        ) -> dict:
    """Phase-1 Fundamental Qualification Gate — Four M's proxies,
    five-numbers growth table, initial screen, valuation status. The
    verdict is research output; it never creates or sizes an order."""
    from app.services import qualification as qual
    inst = await _inst(db, symbol)
    return await qual.gate_with_price(db, inst)


@router.get("/decision/{symbol}")
async def decision(symbol: str, db: AsyncSession = Depends(get_db)
                   ) -> dict:
    """Step-25 Decision Object — the standardized machine-readable
    verdict: stage results (quality/valuation/technical/margin/
    portfolio/sleeve), confidence, blocking reasons, risk flags,
    CIO advisory (never a gate override), approval_required=True."""
    from app.services import decision_object as dobj
    inst = await _inst(db, symbol)
    return await dobj.decision_object(db, inst)
