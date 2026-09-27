from datetime import date

from sqlalchemy import (
    JSON,
    Date,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, ProvenanceMixin, TimestampMixin


class FinancialStatement(Base, IdMixin, TimestampMixin):
    """Statement-level metadata; line items live as observations."""

    __tablename__ = "financial_statements"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id", "statement", "period_end", "source", "form"
        ),
    )

    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    statement: Mapped[str]  # income|balance|cashflow
    period_end: Mapped[date] = mapped_column(Date, index=True)
    period: Mapped[str]  # Q1..Q4|FY
    form: Mapped[str | None]  # 10-K|10-Q|8-K…
    filed_at: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str]
    source_ref: Mapped[str | None]
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class FundamentalObservation(Base, IdMixin, ProvenanceMixin, TimestampMixin):
    """One fact: e.g. (instrument, 'Revenue', FY2024) -> value.
    Point-in-time: readers filter published_at <= as_of. Restatements are
    new rows linked via supersedes_id — history is never overwritten."""

    __tablename__ = "fundamental_observations"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "concept",
            "period_end",
            "source",
            "published_at",
        ),
    )

    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    concept: Mapped[str] = mapped_column(index=True)  # e.g. us-gaap:Revenue
    value: Mapped[float] = mapped_column(Numeric(28, 6))
    unit: Mapped[str | None]  # USD|shares|pure ratio
    currency: Mapped[str | None] = mapped_column(String(3))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date, index=True)
    fiscal_period: Mapped[str | None]  # FY|Q1..Q4
