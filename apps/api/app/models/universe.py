from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TenantMixin, TimestampMixin


class Universe(Base, IdMixin, TenantMixin, TimestampMixin):
    """Hierarchical universe tiers (Part 2):
    global → eligible → approved → expansion."""

    __tablename__ = "universes"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    name: Mapped[str]  # global|eligible|approved|expansion:<label>
    tier: Mapped[str]  # global|eligible|approved
    description: Mapped[str | None]


class UniverseMembership(Base, IdMixin, TimestampMixin):
    __tablename__ = "universe_memberships"
    __table_args__ = (UniqueConstraint("universe_id", "instrument_id"),)

    universe_id: Mapped[str] = mapped_column(
        ForeignKey("universes.id", ondelete="CASCADE"), index=True
    )
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(default="active")  # active|suspended


class Watchlist(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "watchlists"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    name: Mapped[str]
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class WatchlistItem(Base, IdMixin, TimestampMixin):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("watchlist_id", "instrument_id"),)

    watchlist_id: Mapped[str] = mapped_column(
        ForeignKey("watchlists.id", ondelete="CASCADE"), index=True
    )
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE")
    )
    note: Mapped[str | None]
    # e.g. "green_zone_below_threshold" — why it's watched not held
    reason_code: Mapped[str | None]
