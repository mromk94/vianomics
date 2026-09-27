"""Part 1 — Investment Mandate service.

Rules:
- mandates are immutable once created; changes produce a new version
- only one active mandate per tenant; new version becomes active when
  its effective_from arrives (or immediately if effective_from=None)
- every change requires a change_note + records changed_by
- downstream engines always read `get_active()` — decision records
  snapshot mandate_version so history is never rewritten
"""

from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.models.identity import User
from app.models.mandate import Mandate
from app.security import audit


class MandateIn(BaseModel):
    investment_split_pct: float = Field(70.0, ge=0, le=100)
    trading_split_pct: float = Field(30.0, ge=0, le=100)
    inv_min_stocks: int = Field(10, ge=1, le=50)
    inv_max_stocks: int = Field(15, ge=1, le=50)
    inv_horizon_years_min: int = Field(3, ge=1)
    inv_horizon_years_max: int = Field(5, ge=1)
    trading_max_stocks: int = Field(5, ge=1, le=20)
    trading_horizon: str = "days_weeks"
    target_return_min: float = 15.0
    target_return_max: float = 20.0
    max_drawdown_pct: float = Field(15.0, gt=0, le=100)
    min_sharpe: float = 1.5
    min_win_rate_pct: float = Field(60.0, ge=0, le=100)
    min_risk_reward: float = Field(1.5, ge=0)
    green_zone_pass_score: int = Field(15, ge=0, le=20)
    margin_of_safety_min_pct: float = Field(20.0, ge=0, le=100)
    extra: dict = Field(default_factory=dict)

    effective_from: datetime | None = None
    change_note: str = Field(min_length=5)

    @model_validator(mode="after")
    def splits_sum_100(self):
        if abs(self.investment_split_pct + self.trading_split_pct - 100) > 0.01:
            raise ValueError("investment + trading split must equal 100")
        return self

    @model_validator(mode="after")
    def ranges_sane(self):
        if self.inv_min_stocks > self.inv_max_stocks:
            raise ValueError("inv_min_stocks must be ≤ inv_max_stocks")
        if self.inv_horizon_years_min > self.inv_horizon_years_max:
            raise ValueError("horizon min must be ≤ max")
        if self.target_return_min > self.target_return_max:
            raise ValueError("return target min must be ≤ max")
        return self


async def get_active(db: AsyncSession, tenant_id: str = "default") -> Mandate | None:
    """The mandate in force NOW — latest version whose effective_from
    (or creation time) has passed. Future-dated versions don't take
    effect until their date arrives."""
    return await as_of(db, utcnow(), tenant_id)


async def as_of(db: AsyncSession, at: datetime, tenant_id: str = "default") -> Mandate | None:
    """The mandate in force at a historical timestamp."""
    rows = (
        await db.execute(
            select(Mandate)
            .where(Mandate.tenant_id == tenant_id)
            .order_by(Mandate.version.asc())
        )
    ).scalars().all()
    chosen = None
    for m in rows:
        eff = m.effective_from or m.created_at
        if eff <= at:
            chosen = m
    return chosen


async def create_version(
    db: AsyncSession,
    body: MandateIn,
    actor: User,
    tenant_id: str = "default",
) -> Mandate:
    current = (
        await db.execute(
            select(Mandate).where(Mandate.tenant_id == tenant_id)
        )
    ).scalars().all()
    next_version = max((m.version for m in current), default=0) + 1

    m = Mandate(
        version=next_version,
        is_active=True,
        effective_from=body.effective_from,
        changed_by=actor.id,
        change_note=body.change_note,
        **{k: v for k, v in body.model_dump().items()
           if k not in {"effective_from", "change_note"}},
    )
    # Retire previous active versions (they remain queryable via as_of)
    for old in current:
        old.is_active = False
    db.add(m)
    await audit(
        db, action="mandate.version", actor=actor,
        entity_type="mandate", entity_id=m.id,
        detail={"version": next_version, "note": body.change_note},
        tenant_id=tenant_id,
    )
    return m


def to_dict(m: Mandate) -> dict:
    return {
        "id": m.id,
        "version": m.version,
        "is_active": m.is_active,
        "effective_from": m.effective_from.isoformat() if m.effective_from else None,
        "changed_by": m.changed_by,
        "change_note": m.change_note,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "investment_split_pct": m.investment_split_pct,
        "trading_split_pct": m.trading_split_pct,
        "inv_min_stocks": m.inv_min_stocks,
        "inv_max_stocks": m.inv_max_stocks,
        "inv_horizon_years_min": m.inv_horizon_years_min,
        "inv_horizon_years_max": m.inv_horizon_years_max,
        "trading_max_stocks": m.trading_max_stocks,
        "trading_horizon": m.trading_horizon,
        "target_return_min": m.target_return_min,
        "target_return_max": m.target_return_max,
        "max_drawdown_pct": m.max_drawdown_pct,
        "min_sharpe": m.min_sharpe,
        "min_win_rate_pct": m.min_win_rate_pct,
        "min_risk_reward": m.min_risk_reward,
        "green_zone_pass_score": m.green_zone_pass_score,
        "margin_of_safety_min_pct": m.margin_of_safety_min_pct,
        "extra": m.extra or {},
    }
