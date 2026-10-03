from datetime import date, datetime

from sqlalchemy import (
    Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin


class Exchange(Base, IdMixin, TimestampMixin):
    __tablename__ = "exchanges"
    code: Mapped[str] = mapped_column(String, unique=True)  # NASDAQ, NYSE…
    name: Mapped[str]
    country: Mapped[str] = mapped_column(default="US")
    currency: Mapped[str] = mapped_column(String(3), default="USD")


class Sector(Base, IdMixin, TimestampMixin):
    __tablename__ = "sectors"
    name: Mapped[str] = mapped_column(String, unique=True)  # GICS L1
    industries: Mapped[list["Industry"]] = relationship(back_populates="sector")


class Industry(Base, IdMixin, TimestampMixin):
    __tablename__ = "industries"
    __table_args__ = (UniqueConstraint("sector_id", "name"),)
    sector_id: Mapped[str] = mapped_column(ForeignKey("sectors.id"))
    name: Mapped[str]
    sector: Mapped[Sector] = relationship(back_populates="industries")


class Instrument(Base, IdMixin, TimestampMixin):
    """Canonical security record. Identity is the row; tickers/CIK/FIGI
    are rows in instrument_identifiers so renames never rewrite history."""

    __tablename__ = "instruments"
    __table_args__ = (UniqueConstraint("exchange_id", "symbol"),)

    symbol: Mapped[str] = mapped_column(index=True)
    name: Mapped[str]
    asset_class: Mapped[str] = mapped_column(
        default="equity", index=True
    )  # equity|future|commodity|crypto
    exchange_id: Mapped[str | None] = mapped_column(ForeignKey("exchanges.id"))
    sector_id: Mapped[str | None] = mapped_column(ForeignKey("sectors.id"))
    industry_id: Mapped[str | None] = mapped_column(ForeignKey("industries.id"))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    is_active: Mapped[bool] = mapped_column(default=True)
    # Listing state: active|suspended|delisted — delisted securities stay
    # queryable (history/decisions) but drop out of eligibility.
    listing_status: Mapped[str] = mapped_column(default="active", index=True)
    delisted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # Contract specification (VAIIP Instrument Master — never
    # hard-code contractSize=1). Equity defaults: CS=1, tick 0.01.
    contract_size: Mapped[float] = mapped_column(
        Numeric(24, 8), default=1.0)
    tick_size: Mapped[float | None] = mapped_column(Numeric(24, 8))
    tick_value: Mapped[float | None] = mapped_column(Numeric(24, 8))
    # Broker margin: fraction of notional required as collateral.
    # 0 or NULL = cash instrument (no margin); 0.20 = 5x max leverage.
    margin_rate: Mapped[float | None] = mapped_column(Numeric(10, 6))
    maintenance_margin_rate: Mapped[float | None] = mapped_column(
        Numeric(10, 6))
    # Eligibility inputs — populated by market-data ingestion
    market_cap: Mapped[float | None] = mapped_column(Numeric(24, 2))
    avg_dollar_volume_30d: Mapped[float | None] = mapped_column(Numeric(24, 2))
    identifiers: Mapped[list["InstrumentIdentifier"]] = relationship(
        back_populates="instrument", lazy="selectin"
    )


class InstrumentIdentifier(Base, IdMixin, TimestampMixin):
    __tablename__ = "instrument_identifiers"
    __table_args__ = (UniqueConstraint("scheme", "value"),)

    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    instrument: Mapped[Instrument] = relationship(back_populates="identifiers")
    scheme: Mapped[str]  # ticker|cik|figi|isin|cusip
    value: Mapped[str]
    is_primary: Mapped[bool] = mapped_column(default=False)


class CorporateAction(Base, IdMixin, TimestampMixin):
    __tablename__ = "corporate_actions"
    __table_args__ = (
        UniqueConstraint("instrument_id", "kind", "ex_date", "ratio"),
    )

    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str]  # split|dividend|spinoff|merger
    ex_date: Mapped[date] = mapped_column(Date)
    ratio: Mapped[float | None] = mapped_column(Numeric(18, 8))
    amount: Mapped[float | None] = mapped_column(Numeric(18, 6))
    currency: Mapped[str | None] = mapped_column(String(3))
    source: Mapped[str]
