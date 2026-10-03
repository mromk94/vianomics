from sqlalchemy import JSON, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, utcnow


class PyramidTradeRec(Base, IdMixin, TimestampMixin):
    """Persisted pyramid state machine — one row per pyramid trade;
    events list is append-only JSON log of transitions."""

    __tablename__ = "pyramid_trades"
    __table_args__ = (Index("ix_pyr_instr", "instrument_id"),)

    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    portfolio_id: Mapped[str | None] = mapped_column(
        ForeignKey("portfolios.id"))
    state: Mapped[str]
    entry: Mapped[float]
    atr_initial: Mapped[float]
    shares: Mapped[int]
    stop: Mapped[float]
    target1: Mapped[float]
    t2_policy: Mapped[str]
    additions: Mapped[int] = mapped_column(default=0)
    engine_version: Mapped[str] = mapped_column(default="risk-pyramid/v2.0")
    events: Mapped[list] = mapped_column(JSON, default=list)
    params: Mapped[dict] = mapped_column(JSON, default=dict)


class RiskCheck(Base, IdMixin, TimestampMixin):
    """Every order-gate evaluation — the audit trail that proves no
    order bypassed limits."""

    __tablename__ = "risk_checks"

    symbol: Mapped[str]
    side: Mapped[str]
    notional: Mapped[float]
    allowed: Mapped[bool]
    breaches: Mapped[list] = mapped_column(JSON)
    limits_snapshot: Mapped[dict] = mapped_column(JSON)
    engine_version: Mapped[str]
    checked_at: Mapped[object] = mapped_column(DateTime(timezone=True),
                                               default=utcnow)
    checked_by: Mapped[str | None]


class PmDecision(Base, IdMixin, TimestampMixin):
    """PM Decision Engine audit — every ENTER/ADD/HOLD/REDUCE/EXIT/
    BLOCK/REVIEW with the full input snapshot and book context
    (spec §27 DecisionAudit)."""

    __tablename__ = "pm_decisions"

    symbol: Mapped[str]
    decision: Mapped[str]                # ENTER|ADD|HOLD|REDUCE|EXIT|BLOCK|REVIEW
    reason: Mapped[str]
    inputs: Mapped[dict] = mapped_column(JSON)   # thesis/valuation/technical/risk/portfolio checks
    equity: Mapped[float | None]
    exposure: Mapped[float | None]
    open_risk: Mapped[float | None]
    leverage: Mapped[float | None]
    rr: Mapped[float | None]
    engine_version: Mapped[str] = mapped_column(default="risk-pyramid/v2.0")
    decided_by: Mapped[str | None]


class LimitConfig(Base, IdMixin, TimestampMixin):
    """Versioned risk-limit config — operator-editable; check_order
    reads the newest version. Every version immutable (audit)."""

    __tablename__ = "limit_configs"

    version: Mapped[int]
    payload: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[str | None]
    note: Mapped[str | None]
