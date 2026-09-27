"""Idempotent ingestion primitives.

Idempotency is enforced by natural unique constraints: re-running a job
re-finds the same natural key and skips rather than duplicating. New
revisions are INSERTed with supersedes_id — never UPDATEd in place.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase


async def get_or_create(
    session: AsyncSession,
    model: type[DeclarativeBase],
    natural_key: dict[str, Any],
    defaults: dict[str, Any] | None = None,
) -> tuple[Any, bool]:
    """(row, created). Portable idempotent insert: SELECT then INSERT.
    Unique constraints are the backstop for concurrent duplicates."""
    stmt = select(model)
    for k, v in natural_key.items():
        stmt = stmt.where(getattr(model, k) == v)
    row = (await session.execute(stmt)).scalars().first()
    if row is not None:
        return row, False
    row = model(**natural_key, **(defaults or {}))
    session.add(row)
    await session.flush()
    return row, True


async def supersede(
    session: AsyncSession,
    model: type[DeclarativeBase],
    natural_key: dict[str, Any],
    new_values: dict[str, Any],
) -> tuple[Any, str]:
    """Insert a new revision of an observation.

    If the natural key already exists with identical payload → 'unchanged'.
    If it exists with different values → insert new row whose
    supersedes_id points at the old row and mark old row 'restated'.
    Returns (row, 'unchanged'|'revised'|'inserted').
    """
    stmt = select(model)
    for k, v in natural_key.items():
        stmt = stmt.where(getattr(model, k) == v)
    existing = (await session.execute(stmt)).scalars().first()

    if existing is None:
        row = model(**natural_key, **new_values)
        session.add(row)
        await session.flush()
        return row, "inserted"

    changed = any(
        getattr(existing, k) != v
        for k, v in new_values.items()
        if k != "supersedes_id"
    )
    if not changed:
        return existing, "unchanged"

    for k, v in new_values.items():
        if k == "value":
            continue  # never rewrite the historical value in place
        setattr(existing, k, v)
    existing.quality = "restated"
    row = model(
        **natural_key, **new_values, supersedes_id=existing.id
    )
    session.add(row)
    await session.flush()
    return row, "revised"
