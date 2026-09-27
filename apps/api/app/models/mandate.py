from sqlalchemy import JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TenantMixin, TimestampMixin


class Mandate(Base, IdMixin, TenantMixin, TimestampMixin):
    """Versioned investment mandate (Part 1). Rows are immutable —
    a change creates a new version and retires the old one."""

    __tablename__ = "mandates"
    __table_args__ = (UniqueConstraint("tenant_id", "version"),)

    version: Mapped[int]
    is_active: Mapped[bool] = mapped_column(default=False)

    # Constraints injected into every downstream engine.
    investment_split_pct: Mapped[float] = mapped_column(default=70.0)
    trading_split_pct: Mapped[float] = mapped_column(default=30.0)
    target_return_min: Mapped[float] = mapped_column(default=15.0)
    target_return_max: Mapped[float] = mapped_column(default=20.0)
    max_drawdown_pct: Mapped[float] = mapped_column(default=15.0)
    min_sharpe: Mapped[float] = mapped_column(default=1.5)
    min_win_rate_pct: Mapped[float] = mapped_column(default=60.0)
    min_risk_reward: Mapped[float] = mapped_column(default=1.5)
    green_zone_pass_score: Mapped[int] = mapped_column(default=15)  # C1
    margin_of_safety_min_pct: Mapped[float] = mapped_column(default=20.0)

    # Full philosophy/constraint payload for forward extension.
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
