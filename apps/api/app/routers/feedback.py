from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services import feedback as fb

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.get("/accuracy")
async def accuracy(min_age_days: int = 1,
                   db: AsyncSession = Depends(get_db)):
    """Expected vs realized per decision + per-agent accuracy.
    Realized = latest close vs decision-date price — not P&L."""
    return await fb.decision_feedback(db, min_age_days)


@router.get("/attribution")
async def attribution(db: AsyncSession = Depends(get_db)):
    """Unrealized marks on paper/broker fills — labeled by source."""
    return await fb.trade_attribution(db)
