from sqlalchemy import JSON, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class ValuationRun(Base, IdMixin, TimestampMixin):
    """Versioned valuation — inputs fully stored so every output is
    reproducible from inputs + METHODOLOGY_VERSION."""

    __tablename__ = "valuation_runs"
    __table_args__ = (
        UniqueConstraint("group_id", "version"),
        Index("ix_val_instr", "instrument_id", "created_at"),
    )

    group_id: Mapped[str]
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instruments.id")
    )
    version: Mapped[int]
    methodology: Mapped[str]
    mandate_version: Mapped[int | None]
    created_by: Mapped[str | None]
    # complete assumption set (price, growth, rates, mos, sources)
    inputs: Mapped[dict] = mapped_column(JSON)
    # sticker, buy, dcf, reverse, sensitivity, five_numbers, mos
    outputs: Mapped[dict] = mapped_column(JSON)
