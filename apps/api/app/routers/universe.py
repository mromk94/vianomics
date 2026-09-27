from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.identity import User
from app.models.instruments import Instrument
from app.models.universe import Watchlist, WatchlistItem
from app.security import audit, require
from app.services import universe as svc

router = APIRouter(prefix="/universe", tags=["universe"])


@router.get("")
async def hierarchy(db: AsyncSession = Depends(get_db)) -> dict:
    """Global → Eligible → Approved counts + securities total."""
    return await svc.universe_stats(db)


@router.get("/instruments")
async def search(
    q: str | None = Query(None),
    asset_class: str | None = Query(None),
    include_inactive: bool = Query(False),
    eligible_only: bool = Query(False),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    return await svc.search_instruments(
        db, q=q, asset_class=asset_class,
        include_inactive=include_inactive, eligible_only=eligible_only,
        limit=limit,
    )


@router.get("/instruments/{symbol}")
async def detail(symbol: str, db: AsyncSession = Depends(get_db)) -> dict:
    inst = (
        await db.execute(
            select(Instrument).where(Instrument.symbol == symbol.upper())
        )
    ).scalar_one_or_none()
    if inst is None:
        raise HTTPException(404, f"{symbol} not in security master")
    return await svc.instrument_detail(db, inst)


class MembershipIn(BaseModel):
    universe: str = Field(pattern="^(global|eligible|approved)$")
    status: str = Field(pattern="^(active|suspended|excluded)$")
    reason: str | None = None


@router.post("/instruments/{symbol}/membership")
async def set_membership(
    symbol: str,
    body: MembershipIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("universe:write")),
) -> dict:
    inst = (
        await db.execute(
            select(Instrument).where(Instrument.symbol == symbol.upper())
        )
    ).scalar_one_or_none()
    if inst is None:
        raise HTTPException(404, f"{symbol} not in security master")
    if body.status == "excluded" and not body.reason:
        raise HTTPException(422, "exclusion requires a reason")
    m = await svc.set_membership(
        db, universe_name=body.universe, instrument=inst,
        status=body.status, reason=body.reason,
    )
    await audit(
        db, action="universe.membership", actor=user,
        entity_type="instrument", entity_id=inst.id,
        detail={"universe": body.universe, "status": body.status,
                "reason": body.reason},
    )
    await db.commit()
    return {"universe": body.universe, "status": m.status, "reason": m.reason}


@router.get("/watchlists")
async def list_watchlists(db: AsyncSession = Depends(get_db)) -> list[dict]:
    return await svc.watchlists(db)


class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.post("/watchlists", status_code=201)
async def create_watchlist(
    body: WatchlistIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("universe:write")),
) -> dict:
    w = Watchlist(name=body.name, owner_id=user.id)
    db.add(w)
    await db.commit()
    return {"id": w.id, "name": w.name, "items": []}


class WatchlistItemIn(BaseModel):
    symbol: str
    note: str | None = None
    reason_code: str | None = None


@router.post("/watchlists/{watchlist_id}/items", status_code=201)
async def add_watchlist_item(
    watchlist_id: str,
    body: WatchlistItemIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("universe:write")),
) -> dict:
    w = (
        await db.execute(select(Watchlist).where(Watchlist.id == watchlist_id))
    ).scalar_one_or_none()
    if w is None:
        raise HTTPException(404, "watchlist not found")
    inst = (
        await db.execute(
            select(Instrument).where(Instrument.symbol == body.symbol.upper())
        )
    ).scalar_one_or_none()
    if inst is None:
        raise HTTPException(404, f"{body.symbol} not in security master")
    item = WatchlistItem(
        watchlist_id=w.id, instrument_id=inst.id,
        note=body.note, reason_code=body.reason_code,
    )
    db.add(item)
    await db.commit()
    return {"symbol": inst.symbol, "note": item.note}
