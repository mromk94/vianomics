from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.identity import User
from app.models.ops import Alert
from app.security import audit, require
from app.services import monitoring as mon

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


@router.post("/scan", status_code=202)
async def scan(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("research:run")),
):
    """Run all monitor checks now — returns counts of newly emitted
    alerts (deduped within the 24h cooldown)."""
    res = await mon.run_checks(db)
    await db.commit()
    return res


@router.get("/alerts")
async def alerts(status: str = "active", limit: int = 50,
                 db: AsyncSession = Depends(get_db)):
    q = select(Alert).order_by(Alert.created_at.desc()).limit(limit)
    if status != "all":
        q = q.where(Alert.status == status)
    rows = (await db.execute(q)).scalars().all()
    return [
        {"id": a.id, "severity": a.severity, "source": a.source,
         "message": a.message, "status": a.status,
         "context": a.context,
         "created_at": a.created_at.isoformat(),
         "resolved_at": a.resolved_at.isoformat()
         if a.resolved_at else None}
        for a in rows
    ]


@router.post("/alerts/{alert_id}/ack")
async def ack(alert_id: str,
              db: AsyncSession = Depends(get_db),
              user: User = Depends(require("research:run"))):
    a = await db.get(Alert, alert_id)
    if a is None:
        raise HTTPException(404, "alert not found")
    a.status = "acknowledged"
    a.acked_by = user.id
    await db.commit()
    return {"id": a.id, "status": a.status}


@router.post("/alerts/{alert_id}/resolve")
async def resolve(alert_id: str,
                  db: AsyncSession = Depends(get_db),
                  user: User = Depends(require("research:run"))):
    a = await db.get(Alert, alert_id)
    if a is None:
        raise HTTPException(404, "alert not found")
    a.status = "resolved"
    a.resolved_at = datetime.now(timezone.utc)
    await audit(db, action="alert.resolve", actor=user,
                entity_type="alert", entity_id=a.id)
    await db.commit()
    return {"id": a.id, "status": a.status}
