from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.identity import User
from app.models.mandate import Mandate
from app.security import require
from app.services import mandate as svc
from app.services.mandate import MandateIn

router = APIRouter(prefix="/mandate", tags=["mandate"])


@router.get("/active")
async def active(db: AsyncSession = Depends(get_db)) -> dict:
    m = await svc.get_active(db)
    if m is None:
        raise HTTPException(404, "no mandate configured")
    return svc.to_dict(m)


@router.get("/versions")
async def versions(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("mandate:read")),
) -> list[dict]:
    rows = (
        await db.execute(
            select(Mandate).order_by(Mandate.version.desc())
        )
    ).scalars().all()
    return [svc.to_dict(m) for m in rows]


@router.get("/as-of")
async def as_of(
    at: datetime,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("mandate:read")),
) -> dict:
    m = await svc.as_of(db, at)
    if m is None:
        raise HTTPException(404, f"no mandate in force at {at}")
    return svc.to_dict(m)


@router.post("/versions", status_code=201)
async def create_version(
    body: MandateIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("mandate:write")),
) -> dict:
    try:
        m = await svc.create_version(db, body, user)
    except ValueError as e:
        raise HTTPException(422, str(e))
    await db.commit()
    return svc.to_dict(m)
