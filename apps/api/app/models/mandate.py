from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TenantMixin, TimestampMixin


class Mandate(Base, IdMixin, TenantMixin, TimestampMixin):
    """Versioned investment mandate (Part 1). Rows are immutable —
    a change creates a new version and retires the old one.

    Performance objectives (return/Sharpe/win-rate) are TARGETS, not
    guarantees — engines may measure against them, never promise them.
    """

    __tablename__ = "mandates"
    __table_args__ = (UniqueConstraint("tenant_id", "version"),)

    version: Mapped[int]
    is_active: Mapped[bool] = mapped_column(default=False)
    effective_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    changed_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id")
    )
    change_note: Mapped[str | None]  # mandatory on new versions

    # ── Capital allocation ──
    investment_split_pct: Mapped[float] = mapped_column(default=70.0)
    trading_split_pct: Mapped[float] = mapped_column(default=30.0)

    # ── Investment portfolio structure ──
    inv_min_stocks: Mapped[int] = mapped_column(default=10)
    inv_max_stocks: Mapped[int] = mapped_column(default=15)
    inv_horizon_years_min: Mapped[int] = mapped_column(default=3)
    inv_horizon_years_max: Mapped[int] = mapped_column(default=5)

    # ── Trading portfolio structure ──
    trading_max_stocks: Mapped[int] = mapped_column(default=5)
    trading_horizon: Mapped[str] = mapped_column(default="days_weeks")

    # ── Objectives (targets, not guarantees) ──
    target_return_min: Mapped[float] = mapped_column(default=15.0)
    target_return_max: Mapped[float] = mapped_column(default=20.0)
    max_drawdown_pct: Mapped[float] = mapped_column(default=15.0)
    min_sharpe: Mapped[float] = mapped_column(default=1.5)
    min_win_rate_pct: Mapped[float] = mapped_column(default=60.0)
    min_risk_reward: Mapped[float] = mapped_column(default=1.5)

    # ── Gates ──
    green_zone_pass_score: Mapped[int] = mapped_column(default=15)  # C1
    margin_of_safety_min_pct: Mapped[float] = mapped_column(default=20.0)

    # Full philosophy/constraint payload for forward extension.
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
