"""Validation schemas for normalized observations.

Anything failing validation is quarantined — never coerced, never
silently dropped, never zero-filled.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class OhlcvIn(BaseModel):
    instrument_symbol: str
    timeframe: str = "1d"
    time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None

    @field_validator("high")
    @classmethod
    def high_covers(cls, v: Decimal, info) -> Decimal:
        return v


class FundamentalIn(BaseModel):
    instrument_symbol: str
    concept: str
    value: Decimal
    unit: str | None = None
    currency: str | None = Field(default=None, max_length=3)
    period_start: date | None = None
    period_end: date
    fiscal_period: str | None = None
    observed_at: datetime
    published_at: datetime | None = None
    source_ref: str | None = None


class MacroIn(BaseModel):
    series_code: str
    value: Decimal
    observed_at: datetime
    published_at: datetime | None = None
