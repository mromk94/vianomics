from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, ProvenanceMixin, TimestampMixin, utcnow


class OhlcvBar(Base, IdMixin, TimestampMixin):
    """Market bars. `adjusted` distinguishes raw vs corporate-action-
    adjusted series; both may coexist. Point-in-time via `time`."""

    __tablename__ = "ohlcv_bars"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id", "timeframe", "time", "source", "adjusted"
        ),
    )

    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    timeframe: Mapped[str]  # 1d|3d|1w|1mo
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    open: Mapped[float] = mapped_column(Numeric(20, 6))
    high: Mapped[float] = mapped_column(Numeric(20, 6))
    low: Mapped[float] = mapped_column(Numeric(20, 6))
    close: Mapped[float] = mapped_column(Numeric(20, 6))
    volume: Mapped[float | None] = mapped_column(Numeric(28, 2))
    adjusted: Mapped[bool] = mapped_column(default=False)
    source: Mapped[str]
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class MacroSeries(Base, IdMixin, TimestampMixin):
    __tablename__ = "macro_series"
    __table_args__ = (UniqueConstraint("source", "code"),)

    source: Mapped[str]  # fred
    code: Mapped[str]  # GDP, CPIAUCSL, DGS10…
    name: Mapped[str]
    frequency: Mapped[str | None]
    unit: Mapped[str | None]
    category: Mapped[str | None]  # gdp|inflation|rates|pmi|employment…


class MacroObservation(Base, IdMixin, ProvenanceMixin, TimestampMixin):
    __tablename__ = "macro_observations"
    __table_args__ = (
        UniqueConstraint("series_id", "observed_at", "published_at"),
    )

    series_id: Mapped[str] = mapped_column(
        ForeignKey("macro_series.id", ondelete="CASCADE"), index=True
    )
    value: Mapped[float] = mapped_column(Numeric(24, 6))


class EconomicRelease(Base, IdMixin, TimestampMixin):
    """Release calendar metadata (CPI print, FOMC, payrolls…)."""

    __tablename__ = "economic_releases"
    __table_args__ = (UniqueConstraint("series_id", "release_at"),)

    series_id: Mapped[str | None] = mapped_column(
        ForeignKey("macro_series.id", ondelete="SET NULL")
    )
    title: Mapped[str]
    release_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    period: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str]
