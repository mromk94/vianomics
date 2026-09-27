from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TenantMixin, TimestampMixin, utcnow


class ScreeningPolicy(Base, IdMixin, TimestampMixin):
    """Versioned threshold set — every screen records the policy used."""

    __tablename__ = "screening_policies"
    __table_args__ = (UniqueConstraint("tenant_id", "version"),)

    tenant_id: Mapped[str] = mapped_column(default="default")
    version: Mapped[int]
    is_active: Mapped[bool] = mapped_column(default=True)
    change_note: Mapped[str | None]
    # All thresholds live here — see services/green_zone.POLICY_DEFAULTS
    params: Mapped[dict] = mapped_column(JSON)


class ScreeningRun(Base, IdMixin, TimestampMixin):
    __tablename__ = "screening_runs"

    universe: Mapped[str] = mapped_column(default="approved")
    policy_version: Mapped[int]
    mandate_version: Mapped[int | None]
    status: Mapped[str] = mapped_column(
        default="running"
    )  # running|complete|failed
    instruments: Mapped[int] = mapped_column(default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    error: Mapped[str | None]


class ScreeningResult(Base, IdMixin, TimestampMixin):
    """One instrument's Green Zone screen — criteria detail is the
    audit record (status + evidence per criterion)."""

    __tablename__ = "screening_results"
    __table_args__ = (
        UniqueConstraint("run_id", "instrument_id"),
        Index("ix_screening_instr", "instrument_id", "created_at"),
    )

    run_id: Mapped[str] = mapped_column(
        ForeignKey("screening_runs.id", ondelete="CASCADE"), index=True
    )
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    score: Mapped[float]  # sum of criterion scores (pass=1, review=0.5)
    applicable: Mapped[int]  # criteria not N/A
    qualified: Mapped[bool] = mapped_column(default=False)
    verdict: Mapped[str]  # pass|fail|review|insufficient_data|blocked_by_risk
    blocked_reasons: Mapped[list] = mapped_column(JSON, default=list)
    # [{key, name, status, score, formula, evidence, industry_variant,
    #   review_required}]
    criteria: Mapped[list] = mapped_column(JSON)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
