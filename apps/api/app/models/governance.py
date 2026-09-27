from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String
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


class OrderTicket(Base, IdMixin, TimestampMixin):
    """Part 23 — proposed order pending/approved by a human.
    params_hash binds approval to exact parameters — any material
    change (qty, price band, stop) or changed risk conditions
    invalidates the approval."""

    __tablename__ = "order_tickets"

    decision_id: Mapped[str] = mapped_column(
        ForeignKey("decision_records.id"), index=True)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instruments.id"))
    portfolio_id: Mapped[str | None] = mapped_column(
        ForeignKey("portfolios.id"))
    side: Mapped[str]
    order_type: Mapped[str] = mapped_column(default="market")
    quantity: Mapped[float]
    limit_price: Mapped[float | None]
    est_notional: Mapped[float | None]
    est_fees: Mapped[float | None]
    stop: Mapped[float | None]
    target1: Mapped[float | None]
    target2: Mapped[float | None]
    reward_risk: Mapped[float | None]
    status: Mapped[str] = mapped_column(default="proposed")
    # proposed|approved|rejected|expired|submitted|filled|cancelled
    params_hash: Mapped[str] = mapped_column(String(64))
    risk_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    exposure_before: Mapped[dict] = mapped_column(JSON, default=dict)
    exposure_after: Mapped[dict] = mapped_column(JSON, default=dict)
    cio_report: Mapped[dict] = mapped_column(JSON, default=dict)
    data_freshness: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_policy_version: Mapped[str] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))


class ExitSignal(Base, IdMixin, TimestampMixin):
    """Part 24 — exit lifecycle record. A signal is not a fill."""

    __tablename__ = "exit_signals"

    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    position_id: Mapped[str | None] = mapped_column(
        ForeignKey("positions.id"))
    cls: Mapped[str]              # fundamental|valuation|technical|risk|market|thesis
    stage: Mapped[str]            # signal|triggered|proposed_order|submitted|filled|closed
    action: Mapped[str]
    reason: Mapped[str | None]
    order_ticket_id: Mapped[str | None] = mapped_column(
        ForeignKey("order_tickets.id"))
    fill_price: Mapped[float | None]
    filled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))


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
    # Snapshot: the mandate version in force when this decision was made —
    # later mandate edits must never retroactively rewrite history.
    mandate_version: Mapped[int | None]
