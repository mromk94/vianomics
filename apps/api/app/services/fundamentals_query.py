"""Concept → aligned fiscal-year series from fundamental_observations.

Only annual (FY, ~360-day) durations are used for growth/level metrics —
never mix quarterly and annual periods for the same series.
Instant concepts (equity, debt, shares) take the latest filing as-of `as_of`.
Flow concepts return a dict {period_end: value} of FY observations.
"""

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fundamentals import FundamentalObservation

FY_MIN_DAYS = 350


async def fy_series(
    db: AsyncSession,
    instrument_id: str,
    concepts: list[str],
    as_of: datetime,
    years: int = 6,
) -> dict[date, float]:
    """FY value per period_end, merged across concept aliases.

    Aliases merge rather than first-non-empty-wins: a stale taxonomy
    (`Revenues`, ends 2010) must not shadow the live one
    (`RevenueFromContractWithCustomerExcludingAssessedTax`). The
    earliest-listed (canonical) concept wins each period_end; later
    aliases only fill ends the canonical ones lack — old-era values
    extend coverage instead of competing. Window filtering happens
    per-concept BEFORE merge so an out-of-window alias can't win."""
    cutoff = as_of.date() - timedelta(days=365 * years)
    out: dict[date, float] = {}
    for concept in concepts:
        rows = (
            await db.execute(
                select(FundamentalObservation)
                .where(
                    FundamentalObservation.instrument_id == instrument_id,
                    FundamentalObservation.concept == concept,
                    FundamentalObservation.published_at.is_not(None),
                    FundamentalObservation.published_at <= as_of,
                    FundamentalObservation.quality != "quarantined",
                    FundamentalObservation.period_end >= cutoff,
                )
                .order_by(
                    FundamentalObservation.period_end.asc(),
                    FundamentalObservation.published_at.desc(),
                )
            )
        ).scalars().all()
        for r in rows:
            # FY-only: duration ≥ ~350 days, or fiscal_period == 'FY'
            if r.period_start:
                if (r.period_end - r.period_start).days < FY_MIN_DAYS:
                    continue
            elif r.fiscal_period != "FY":
                continue
            # later filings supersede earlier ones for same period
            out.setdefault(r.period_end, float(r.value))
    return out


async def latest_instant(
    db: AsyncSession,
    instrument_id: str,
    concepts: list[str],
    as_of: datetime,
) -> float | None:
    """Latest published value for point-in-time concepts (debt, equity…)."""
    for concept in concepts:
        r = (
            await db.execute(
                select(FundamentalObservation)
                .where(
                    FundamentalObservation.instrument_id == instrument_id,
                    FundamentalObservation.concept == concept,
                    FundamentalObservation.published_at.is_not(None),
                    FundamentalObservation.published_at <= as_of,
                    FundamentalObservation.quality != "quarantined",
                )
                .order_by(
                    FundamentalObservation.period_end.desc(),
                    FundamentalObservation.published_at.desc(),
                )
            )
        ).scalars().first()
        if r is not None:
            return float(r.value)
    return None


async def reporting_currency(
    db: AsyncSession,
    instrument_id: str,
    as_of: datetime,
) -> str | None:
    """Currency of the issuer's most recent fundamental observation —
    the unit the financials are reported in. Compared to the
    instrument's trading currency by callers: a TSM ADR prices in USD
    while the 20-F reports TWD, so per-share fundamentals cannot be
    compared to the USD price without FX/ADR-ratio normalization."""
    return (await db.execute(
        select(FundamentalObservation.currency)
        .where(FundamentalObservation.instrument_id == instrument_id,
               FundamentalObservation.currency.is_not(None),
               FundamentalObservation.published_at.is_not(None),
               FundamentalObservation.published_at <= as_of,
               FundamentalObservation.quality != "quarantined")
        .order_by(FundamentalObservation.period_end.desc())
        .limit(1))).scalar_one_or_none()


def last_two(series: dict[date, float]) -> tuple[float | None, float | None]:
    """(previous, latest) consecutive FY values, or Nones."""
    items = sorted(series.items())
    if not items:
        return None, None
    latest = items[-1][1]
    prev = items[-2][1] if len(items) >= 2 else None
    return prev, latest


def series_values(series: dict[date, float], n: int = 3) -> list[float]:
    """Most recent n values, oldest→newest."""
    items = sorted(series.items())
    return [v for _, v in items[-n:]]
