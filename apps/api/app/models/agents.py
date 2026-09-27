from sqlalchemy import JSON, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TenantMixin, TimestampMixin


class AgentDef(Base, IdMixin, TimestampMixin):
    """Registry of agent types (Part 27 matrix)."""

    __tablename__ = "agent_defs"

    key: Mapped[str] = mapped_column(
        unique=True
    )  # fundamental|valuation|rule_one|quant|macro|technical|risk|pm|cio|execution
    name: Mapped[str]
    layer: Mapped[str]  # research|control|capital|executive|execution
    primary_question: Mapped[str]
    version: Mapped[str] = mapped_column(default="1")


class AgentRun(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "agent_runs"

    agent_key: Mapped[str] = mapped_column(index=True)
    instrument_id: Mapped[str | None] = mapped_column(
        ForeignKey("instruments.id"), index=True
    )
    decision_run_id: Mapped[str | None] = mapped_column(index=True)
    model: Mapped[str | None]
    prompt_hash: Mapped[str | None]
    status: Mapped[str] = mapped_column(
        default="pending"
    )  # pending|running|complete|failed
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None]


class AgentOutput(Base, IdMixin, TimestampMixin):
    """Part-28 standardized output — schema-validated before insert."""

    __tablename__ = "agent_outputs"

    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    recommendation: Mapped[str | None]
    score: Mapped[float | None]
    confidence: Mapped[float | None]
    data_quality: Mapped[str | None]
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class EvidenceRef(Base, IdMixin, TimestampMixin):
    """Links an agent claim to source rows — auditability."""

    __tablename__ = "evidence_refs"
    __table_args__ = (
        UniqueConstraint("agent_output_id", "ref_table", "ref_id"),
    )

    agent_output_id: Mapped[str] = mapped_column(
        ForeignKey("agent_outputs.id", ondelete="CASCADE"), index=True
    )
    ref_table: Mapped[str]  # fundamental_observations|ohlcv_bars|…
    ref_id: Mapped[str]
    note: Mapped[str | None]
