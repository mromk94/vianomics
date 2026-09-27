from sqlalchemy import JSON, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class RegimeRun(Base, IdMixin, TimestampMixin):
    """One macro/market regime classification — versioned rules +
    feature values recorded for full explainability/reproducibility."""

    __tablename__ = "regime_runs"
    __table_args__ = (Index("ix_regime_asof", "as_of"),)

    as_of: Mapped[str] = mapped_column(DateTime(timezone=True))
    rules_version: Mapped[str]
    econ_regime: Mapped[str]   # recovery|expansion|slowdown|recession
    market_regime: Mapped[str]  # risk_on|neutral|risk_off
    fear_greed: Mapped[int | None]  # 0-100 proxy composite
    overlay: Mapped[str | None]     # accumulation|cautious|neutral|reduce|aggressive
    vix: Mapped[float | None]
    vix_band: Mapped[str | None]
    # {feature: {value, latest_obs_at, rule_hits}} — the explainable set
    features: Mapped[dict] = mapped_column(JSON)
    rule_hits: Mapped[list] = mapped_column(JSON)
    stale_inputs: Mapped[list] = mapped_column(JSON, default=list)
    # sector weights from rotation map
    sector_preferences: Mapped[dict] = mapped_column(JSON, default=dict)
