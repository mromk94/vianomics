from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class BacktestRun(Base, IdMixin, TimestampMixin):
    """Versioned, reproducible backtest — params + data snapshot hash
    + code version + execution assumptions all stored."""

    __tablename__ = "backtest_runs"

    name: Mapped[str | None]
    universe: Mapped[list] = mapped_column(JSON)     # symbols
    params: Mapped[dict] = mapped_column(JSON)
    engine_version: Mapped[str] = mapped_column(String(64))
    data_snapshot: Mapped[dict] = mapped_column(
        JSON, default=dict)   # {symbol: {bars, first, last}}
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    equity_curve: Mapped[list] = mapped_column(JSON, default=list)
    trades: Mapped[list] = mapped_column(JSON, default=list)
    validation: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(default="complete")
    note: Mapped[str] = mapped_column(
        default="backtest — simulated, not live performance")
