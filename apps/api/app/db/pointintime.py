"""Point-in-time query helpers — backtests and historical views must
only ever see data that was public as of `as_of`."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase


def pit_filter(model: type[DeclarativeBase], as_of: datetime):
    """Only observations publicly available at `as_of`, latest revision
    first. Rows with NULL published_at are treated as not-yet-public and
    excluded (conservative: unknown publication ≠ public)."""
    return (
        select(model)
        .where(model.published_at.is_not(None))
        .where(model.published_at <= as_of)
        .where(model.quality != "quarantined")
        .order_by(model.published_at.desc(), model.ingested_at.desc())
    )


async def latest_as_of(
    session: AsyncSession,
    model: type[DeclarativeBase],
    as_of: datetime,
    **filters,
) -> DeclarativeBase | None:
    """Latest published revision of an observation as of `as_of`."""
    stmt = pit_filter(model, as_of)
    for k, v in filters.items():
        stmt = stmt.where(getattr(model, k) == v)
    return (await session.execute(stmt)).scalars().first()
