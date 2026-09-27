from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.session import get_db
from app.models.identity import User
from app.models.instruments import Instrument
from app.models.valuation import ValuationRun
from app.security import audit, require
from app.services import mandate as mandate_svc
from app.services import valuation_service as vs

router = APIRouter(prefix="/valuation-engine", tags=["valuation"])


async def _inst(db: AsyncSession, symbol: str) -> Instrument:
    inst = (
        await db.execute(
            select(Instrument).where(Instrument.symbol == symbol.upper())
        )
    ).scalar_one_or_none()
    if inst is None:
        raise HTTPException(404, f"{symbol} not in security master")
    return inst


def _run_dict(r: ValuationRun) -> dict:
    return {
        "id": r.id, "version": r.version, "methodology": r.methodology,
        "mandate_version": r.mandate_version,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "inputs": r.inputs, "outputs": r.outputs,
    }


class RunIn(BaseModel):
    price: float = Field(gt=0)
    growth: float = Field(gt=-1, le=2)
    years: int = Field(10, ge=1, le=30)
    discount_rate: float = Field(0.10, gt=0.01, le=0.5)
    terminal_growth: float = Field(0.025, ge=0, le=0.1)
    mos: float | None = Field(None, gt=0, lt=1)
    future_pe: float | None = None
    scenario: str = "base"


@router.get("/inputs/{symbol}")
async def inputs(symbol: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Fundamental anchors + history for the assumption editor."""
    inst = await _inst(db, symbol)
    return await vs.gather_inputs(db, inst, utcnow())


@router.post("/run/{symbol}", status_code=201)
async def run(
    symbol: str,
    body: RunIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("research:run")),
) -> dict:
    inst = await _inst(db, symbol)
    mandate = await mandate_svc.get_active(db)
    r = await vs.run_valuation(
        db, inst, price=body.price, growth=body.growth,
        years=body.years, discount_rate=body.discount_rate,
        terminal_growth=body.terminal_growth, mos=body.mos,
        future_pe=body.future_pe, actor_id=user.id,
        mandate=mandate, scenario=body.scenario,
    )
    await audit(db, action="valuation.run", actor=user,
                entity_type="valuation_run", entity_id=r.id,
                detail={"symbol": inst.symbol, "version": r.version,
                        "scenario": body.scenario})
    await db.commit()
    return _run_dict(r)


@router.get("/{symbol}")
async def latest(symbol: str, db: AsyncSession = Depends(get_db)) -> dict:
    inst = await _inst(db, symbol)
    r = (
        await db.execute(
            select(ValuationRun)
            .where(ValuationRun.instrument_id == inst.id)
            .order_by(ValuationRun.created_at.desc())
        )
    ).scalars().first()
    if r is None:
        raise HTTPException(404, f"no valuation for {symbol}")
    out = _run_dict(r)
    out["symbol"] = inst.symbol
    return out


@router.get("/{symbol}/versions")
async def versions(symbol: str, db: AsyncSession = Depends(get_db)) -> list[dict]:
    inst = await _inst(db, symbol)
    rows = (
        await db.execute(
            select(ValuationRun)
            .where(ValuationRun.instrument_id == inst.id)
            .order_by(ValuationRun.version.desc())
        )
    ).scalars().all()
    return [
        {"id": r.id, "version": r.version, "methodology": r.methodology,
         "scenario": r.inputs.get("scenario"),
         "created_at": r.created_at.isoformat() if r.created_at else None,
         "sticker": r.outputs.get("rule1", {}).get("sticker_price"),
         "dcf": r.outputs.get("dcf", {}).get("per_share")}
        for r in rows
    ]
