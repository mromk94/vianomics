from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TenantMixin, TimestampMixin, utcnow


class RiskAssessment(Base, IdMixin, TimestampMixin):
    """Output of the deterministic Risk Engine (Part 14) feeding the
    Risk Manager Agent (Part 16). Engine computes; agent decides."""

    __tablename__ = "risk_assessments"

    instrument_id: Mapped[str | None] = mapped_column(
        ForeignKey("instruments.id"), index=True
    )
    portfolio_id: Mapped[str | None] = mapped_column(
        ForeignKey("portfolios.id"), index=True
    )
    subject: Mapped[str]  # proposed_trade|existing_position|portfolio
    # 7 dimensions → dict of computed metrics, each with value + limit
    dimensions: Mapped[dict] = mapped_column(JSON, default=dict)
    limit_breaches: Mapped[list] = mapped_column(JSON, default=list)
    verdict: Mapped[str] = mapped_column(
        default="pending"
    )  # pass|warn|block — 'block' is a hard veto
    engine_version: Mapped[str | None]


class Approval(Base, IdMixin, TimestampMixin):
    """Human approval gate (Part 29). Immutable once decided."""

    __tablename__ = "approvals"
    __table_args__ = (Index("ix_approvals_status", "status"),)

    decision_id: Mapped[str | None] = mapped_column(
        ForeignKey("decision_records.id"), index=True
    )
    requested_by: Mapped[str | None]  # agent key / system
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(
        default="pending"
    )  # pending|approved|rejected|expired
    reason: Mapped[str | None]
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )


class DecisionRecord(Base, IdMixin, TenantMixin, TimestampMixin):
    """Investment Decision Record (Part 33) — the audit spine.
    Append-only: amendments are new rows linked by `supersedes_id`."""

    __tablename__ = "decision_records"
    __table_args__ = (Index("ix_decision_instr", "instrument_id", "at"),)

    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    stage: Mapped[str]  # screened|researched|approved|executed|monitored|closed
    verdict: Mapped[str | None]  # approve|reject|watchlist|wait|no_trade|block
    # Snapshot of every gate/agent verdict at decision time.
    gate_results: Mapped[dict] = mapped_column(JSON, default=dict)
    agent_scores: Mapped[dict] = mapped_column(JSON, default=dict)
    numbers: Mapped[dict] = mapped_column(
        JSON, default=dict
    )  # iv, price, mos, atr, entry, stop, t1, t2, size, exposure…
    cio_confidence: Mapped[float | None]
    # Approval link lives on approvals.decision_id — avoids a circular FK.
    supersedes_id: Mapped[str | None]
