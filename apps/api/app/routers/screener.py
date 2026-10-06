from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.fundamentals import FundamentalObservation
from app.models.instruments import Instrument, Sector
from app.models.ops import Job, JobRun
from app.models.screening import (
    ScreeningPolicy,
    ScreeningResult,
    ScreeningRun,
)
from app.models.identity import User
from app.security import audit, require
from app.services import green_zone as gz
from app.services import mandate as mandate_svc

router = APIRouter(prefix="/screener", tags=["screener"])


async def _active_policy(db: AsyncSession) -> ScreeningPolicy:
    p = (
        await db.execute(
            select(ScreeningPolicy)
            .where(ScreeningPolicy.is_active.is_(True))
            .order_by(ScreeningPolicy.version.desc())
        )
    ).scalars().first()
    if p is None:
        p = ScreeningPolicy(
            version=1, is_active=True,
            change_note="default policy", params=gz.POLICY_DEFAULTS,
        )
        db.add(p)
        await db.flush()
    return p


def _result_row(res: ScreeningResult, inst: Instrument) -> dict:
    crits = res.criteria or []
    counts: dict[str, int] = {}
    for c in crits:
        counts[c.get("status", "?")] = counts.get(c.get("status", "?"), 0) + 1
    return {
        "symbol": inst.symbol,
        "name": inst.name,
        "asset_class": inst.asset_class,
        "listing_status": inst.listing_status,
        "score": res.score,
        "applicable": res.applicable,
        "verdict": res.verdict,
        "qualified": res.qualified,
        "blocked_reasons": res.blocked_reasons,
        # per-status criterion tally + names — the row explains itself
        # without opening the drilldown
        "counts": counts,
        "failed": [c["name"] for c in crits
                   if c.get("status") == "fail"],
        "review_items": [c["name"] for c in crits
                         if c.get("status") == "review"],
        "missing": [c["name"] for c in crits
                    if c.get("status") == "insufficient_data"],
        "as_of": res.as_of.isoformat(),
    }


@router.post("/run", status_code=201)
async def run(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("research:run")),
) -> dict:
    """Batch screen the approved universe — recorded as a JobRun."""
    job, _created = None, None
    from app.ingestion.upsert import get_or_create
    job, _created = await get_or_create(
        db, Job, {"key": "screen:green_zone"},
        {"kind": "screening"},
    )
    jrun = JobRun(job_id=job.id)
    db.add(jrun)
    await db.flush()

    policy = await _active_policy(db)
    mandate = await mandate_svc.get_active(db)
    run_obj = await gz.run_screen(db, policy, mandate)
    jrun.status = "success" if run_obj.status == "complete" else "failed"
    jrun.error = run_obj.error
    jrun.records_in = jrun.records_ok = run_obj.instruments
    from app.db.base import utcnow
    jrun.finished_at = utcnow()
    await audit(db, action="screening.run", actor=user,
                entity_type="screening_run", entity_id=run_obj.id,
                detail={"universe": run_obj.universe,
                        "policy_version": policy.version})
    await db.commit()
    return {
        "run_id": run_obj.id,
        "status": run_obj.status,
        "instruments": run_obj.instruments,
        "policy_version": policy.version,
        "mandate_version": mandate.version if mandate else None,
    }


@router.get("/latest")
async def latest(db: AsyncSession = Depends(get_db)) -> dict:
    run_obj = (
        await db.execute(
            select(ScreeningRun).order_by(ScreeningRun.started_at.desc())
        )
    ).scalars().first()
    if run_obj is None or run_obj.status != "complete":
        return {"run": None, "results": []}
    rows = (
        await db.execute(
            select(ScreeningResult, Instrument)
            .join(Instrument, ScreeningResult.instrument_id == Instrument.id)
            .where(ScreeningResult.run_id == run_obj.id)
        )
    ).all()
    return {
        "run": {
            "id": run_obj.id,
            "universe": run_obj.universe,
            "policy_version": run_obj.policy_version,
            "mandate_version": run_obj.mandate_version,
            "started_at": run_obj.started_at.isoformat(),
            "instruments": run_obj.instruments,
        },
        "results": sorted(
            (_result_row(r, i) for r, i in rows),
            key=lambda x: x["score"], reverse=True,
        ),
    }


@router.get("/results/{symbol}")
async def drilldown(symbol: str, db: AsyncSession = Depends(get_db)) -> dict:
    inst = (
        await db.execute(
            select(Instrument).where(Instrument.symbol == symbol.upper())
        )
    ).scalar_one_or_none()
    if inst is None:
        raise HTTPException(404, f"{symbol} not in security master")
    res = (
        await db.execute(
            select(ScreeningResult)
            .where(ScreeningResult.instrument_id == inst.id)
            .order_by(ScreeningResult.created_at.desc())
        )
    ).scalars().first()
    if res is None:
        raise HTTPException(404, f"no screening result for {symbol}")
    run_obj = (
        await db.execute(
            select(ScreeningRun).where(ScreeningRun.id == res.run_id)
        )
    ).scalar_one()
    # freshness: most recent published_at among this instrument's obs
    freshest = (
        await db.execute(
            select(func.max(FundamentalObservation.published_at)).where(
                FundamentalObservation.instrument_id == inst.id
            )
        )
    ).scalar()
    sector = None
    if inst.sector_id:
        sec = await db.get(Sector, inst.sector_id)
        sector = sec.name if sec else None
    return {
        "symbol": inst.symbol,
        "name": inst.name,
        "sector": sector,
        "score": res.score,
        "applicable": res.applicable,
        "verdict": res.verdict,
        "qualified": res.qualified,
        "blocked_reasons": res.blocked_reasons,
        "criteria": res.criteria,
        "policy_version": run_obj.policy_version,
        "mandate_version": run_obj.mandate_version,
        "as_of": res.as_of.isoformat(),
        "data_freshness": freshest.isoformat() if freshest else None,
    }


@router.get("/policies")
async def policies(db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (
        await db.execute(
            select(ScreeningPolicy).order_by(ScreeningPolicy.version.desc())
        )
    ).scalars().all()
    return [
        {"version": p.version, "is_active": p.is_active,
         "change_note": p.change_note, "params": p.params}
        for p in rows
    ]
