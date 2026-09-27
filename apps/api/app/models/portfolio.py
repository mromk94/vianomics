from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TenantMixin, TimestampMixin, utcnow

MONEY = Numeric(28, 8)
QTY = Numeric(28, 8)


class Portfolio(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "portfolios"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    name: Mapped[str]
    kind: Mapped[str] = mapped_column(default="investment")  # investment|trading
    base_currency: Mapped[str] = mapped_column(String(3), default="USD")
    broker: Mapped[str | None]  # ibkr|paper|manual
    external_ref: Mapped[str | None]


class Position(Base, IdMixin, TimestampMixin):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("portfolio_id", "instrument_id"),)

    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    quantity: Mapped[float] = mapped_column(QTY, default=0)
    avg_cost: Mapped[float | None] = mapped_column(MONEY)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    thesis_ref: Mapped[str | None]  # → decision_records / research


class Trade(Base, IdMixin, TimestampMixin):
    """An intended/executed trade. Execution detail lives in orders
    (execution service, later); Trade records the decision-level fill."""

    __tablename__ = "trades"

    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    side: Mapped[str]  # buy|sell
    quantity: Mapped[float] = mapped_column(QTY)
    price: Mapped[float] = mapped_column(MONEY)
    fees: Mapped[float] = mapped_column(MONEY, default=0)
    traded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decision_id: Mapped[str | None] = mapped_column(
        ForeignKey("decision_records.id")
    )
    status: Mapped[str] = mapped_column(
        default="recorded"
    )  # proposed|recorded|settled|void


class LedgerEntry(Base, IdMixin, TimestampMixin):
    """Append-only double-entry-ish cash ledger. Money = Numeric, never float.
    Corrections are new compensating entries, never edits."""

    __tablename__ = "ledger_entries"

    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    trade_id: Mapped[str | None] = mapped_column(ForeignKey("trades.id"))
    kind: Mapped[str]  # deposit|withdrawal|buy|sell|dividend|fee|adjustment
    amount: Mapped[float] = mapped_column(MONEY)  # signed
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    memo: Mapped[str | None]
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
