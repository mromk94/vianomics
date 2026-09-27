import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit naming convention so Alembic autogenerate produces stable,
# reproducible constraint names across dialects.
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class IdMixin:
    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class TenantMixin:
    """Phase-2 multi-tenancy hook. Single-tenant rows use tenant_id='default'.
    Column exists now so Phase 2 doesn't require destructive migrations."""

    tenant_id: Mapped[str] = mapped_column(default="default", index=True)


class ProvenanceMixin:
    """Point-in-time provenance carried by every imported observation.

    observed_at     — the timestamp the observation describes
    published_at    — when the source made it public (drives as-of queries)
    ingested_at     — when VAIIP stored it
    supersedes_id   — revision chain; historical values are never overwritten
    """

    source: Mapped[str] = mapped_column(index=True)
    source_ref: Mapped[str | None]
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    quality: Mapped[str] = mapped_column(default="ok")  # ok|stale|quarantined|restated
    # Revision chain: id of the row this observation supersedes, in the same
    # table. Plain column (no FK) so the mixin stays table-agnostic; writers
    # must set it explicitly and readers resolve it within the same table.
    supersedes_id: Mapped[str | None] = mapped_column(default=None)
