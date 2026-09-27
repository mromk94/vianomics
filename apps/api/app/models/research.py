from sqlalchemy import JSON, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TenantMixin, TimestampMixin


class AnalysisRun(Base, IdMixin, TenantMixin, TimestampMixin):
    """One run of a deterministic engine or research workflow.
    kind: research_dossier|valuation|technical|quant|screening|macro…
    inputs/outputs carry engine-produced data (never agent-invented)."""

    __tablename__ = "analysis_runs"
    __table_args__ = (
        Index("ix_analysis_kind_instrument", "kind", "instrument_id"),
    )

    kind: Mapped[str]
    instrument_id: Mapped[str | None] = mapped_column(
        ForeignKey("instruments.id"), index=True
    )
    engine_version: Mapped[str | None]
    status: Mapped[str] = mapped_column(
        default="pending"
    )  # pending|running|complete|failed
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    outputs: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None]
