from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.session import get_db
from app.models.identity import User
from app.models.instruments import Instrument
from app.models.macro import RegimeRun
from app.models.mandate import Mandate
from app.models.market import MacroObservation, MacroSeries
from app.security import audit, require
from app.services import macro_regime as mr
from app.services import quant_service as qs
from app.services.mandate import get_active

router = APIRouter(tags=["engines"])


# ── quant ──

@router.get("/quant/metrics/{symbol}")
async def quant_metrics(symbol: str, db: AsyncSession = Depends(get_db)):
    inst = (
        await db.execute(
            select(Instrument).where(Instrument.symbol == symbol.upper())
        )
    ).scalar_one_or_none()
    if inst is None:
        raise HTTPException(404, f"{symbol} not in security master")
    return await qs.instrument_metrics(db, inst)


@router.get("/quant/correlation")
async def corr(
    symbols: str = Query("NVDA,MSFT,AMZN,GOOGL,META,AVGO,TSLA,ORCL"),
    db: AsyncSession = Depends(get_db),
):
    return await qs.correlation_matrix(db, symbols.split(","))


# ── macro regime ──

@router.post("/macro/run", status_code=201)
async def macro_run(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("research:run")),
):
    from app.providers.market import fetch_fear_greed
    r = await mr.run_and_persist(db, utcnow(), fetch_fg=fetch_fear_greed)
    await audit(db, action="macro.regime_run", actor=user,
                entity_type="regime_run", entity_id=r.id)
    await db.commit()
    return {"id": r.id, "econ": r.econ_regime, "market": r.market_regime}


@router.get("/macro/current")
async def macro_current(db: AsyncSession = Depends(get_db)):
    r = (
        await db.execute(
            select(RegimeRun).order_by(RegimeRun.as_of.desc())
        )
    ).scalars().first()
    if r is None:
        # compute live, don't persist unauthenticated reads
        from app.providers.market import fetch_fear_greed
        c = await mr.classify(db, utcnow(), fetch_fg=fetch_fear_greed)
        c["persisted"] = False
        return c
    # persisted run exists → serve it directly (one query). The old
    # behavior re-ran classify on every read — ~40 remote round-trips
    # over the prod pooler, past the frontend's 8s abort timeout.
    return mr.run_to_dict(r)


@router.get("/macro/history")
async def macro_history(limit: int = 50, db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(RegimeRun).order_by(RegimeRun.as_of.desc()).limit(limit)
        )
    ).scalars().all()
    return [
        {"as_of": r.as_of.isoformat(), "econ": r.econ_regime,
         "market": r.market_regime, "fear_greed": r.fear_greed,
         "overlay": r.overlay, "vix": r.vix, "vix_band": r.vix_band,
         "rules_version": r.rules_version}
        for r in rows
    ]


@router.get("/macro/indicators")
async def indicators(db: AsyncSession = Depends(get_db)):
    """Latest value + staleness per series."""
    out = []
    for code, (name, cat) in mr.FRED_SERIES.items():
        v, at = await mr._latest_obs(db, code, utcnow())
        stale_days = mr.STALE_AFTER_DAYS.get(code, 45)
        stale = at is None or (utcnow() - at).days > stale_days
        out.append({
            "code": code, "name": name, "category": cat,
            "value": v,
            "observed_at": at.isoformat() if at else None,
            "stale": stale,
        })
    return out


@router.get("/macro/sector-rotation")
async def sector_rotation(db: AsyncSession = Depends(get_db)):
    """Regime-mapped preferences vs mandate sector constraints."""
    r = (
        await db.execute(
            select(RegimeRun).order_by(RegimeRun.as_of.desc())
        )
    ).scalars().first()
    mandate = await get_active(db)
    regime = r.econ_regime if r else "expansion"
    max_sector = (mandate.extra or {}).get("risk_limits", {}).get(
        "sector_limit_pct", 25)
    min_sectors = (mandate.extra or {}).get("risk_limits", {}).get(
        "min_sectors", 5)
    return {
        "regime": regime,
        "favored_sectors": mr.SECTOR_ROTATION.get(regime, []),
        "all_mappings": mr.SECTOR_ROTATION,
        "mandate_constraints": {
            "max_sector_pct": max_sector,
            "min_sectors": min_sectors,
            "reassessment": "quarterly",
        },
        "note": "Preferences are inputs to portfolio analysis, not forecasts.",
    }
