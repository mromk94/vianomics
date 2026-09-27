from sqlalchemy import DateTime, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, utcnow


class BrokerOrderRec(Base, IdMixin, TimestampMixin):
    """Durable broker-order record — idempotency_key unique so a
    duplicate request returns the existing order."""

    __tablename__ = "broker_orders"

    order_ticket_id: Mapped[str] = mapped_column(
        ForeignKey("order_tickets.id"), index=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    broker: Mapped[str]                       # paper|ibkr|…
    broker_order_id: Mapped[str | None]
    idempotency_key: Mapped[str] = mapped_column(String(32), unique=True,
                                                 index=True)
    side: Mapped[str]
    qty: Mapped[float]
    order_type: Mapped[str]
    limit_price: Mapped[float | None]
    status: Mapped[str]                       # OrderState
    filled_qty: Mapped[float] = mapped_column(default=0)
    avg_fill_price: Mapped[float | None]
    commission: Mapped[float | None]
    reject_reason: Mapped[str | None]
    submit_latency_ms: Mapped[int | None]
    risk_policy_version: Mapped[str | None] = mapped_column(String(64))


class ExecutionEvent(Base, IdMixin):
    """Append-only order lifecycle events — audit + reconciliation."""

    __tablename__ = "execution_events"

    order_id: Mapped[str] = mapped_column(index=True)
    stage: Mapped[str]
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[object] = mapped_column(DateTime(timezone=True),
                                       default=utcnow)


class KillSwitch(Base, IdMixin, TimestampMixin):
    """Global trading halt — one row. When enabled every submission
    raises; cancellations still flow through."""

    __tablename__ = "kill_switch"

    enabled: Mapped[bool] = mapped_column(default=False)
    actor: Mapped[str | None]
    reason: Mapped[str | None]
