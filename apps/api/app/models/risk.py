from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin, utcnow


class PyramidTradeRec(Base, IdMixin, TimestampMixin):
    """Persisted pyramid state machine — one row per pyramid trade;
    events list is append-only JSON log of transitions."""

    __tablename__ = "pyramid_trades"
    __table_args__ = (Index("ix_pyr_instr", "instrument_id"),)

    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    portfolio_id: Mapped[str | None] = mapped_column(
        ForeignKey("portfolios.id"))
    state: Mapped[str]
    entry: Mapped[float]
    atr_initial: Mapped[float]
    shares: Mapped[int]
    stop: Mapped[float]
    target1: Mapped[float]
    t2_policy: Mapped[str]
    direction: Mapped[str] = mapped_column(default="long")  # long|short
    additions: Mapped[int] = mapped_column(default=0)
    engine_version: Mapped[str] = mapped_column(default="risk-pyramid/v2.0")
    events: Mapped[list] = mapped_column(JSON, default=list)
    params: Mapped[dict] = mapped_column(JSON, default=dict)


class RiskCheck(Base, IdMixin, TimestampMixin):
    """Every order-gate evaluation — the audit trail that proves no
    order bypassed limits."""

    __tablename__ = "risk_checks"

    symbol: Mapped[str]
    side: Mapped[str]
    notional: Mapped[float]
    allowed: Mapped[bool]
    breaches: Mapped[list] = mapped_column(JSON)
    limits_snapshot: Mapped[dict] = mapped_column(JSON)
    engine_version: Mapped[str]
    checked_at: Mapped[object] = mapped_column(DateTime(timezone=True),
                                               default=utcnow)
    checked_by: Mapped[str | None]


class PmDecision(Base, IdMixin, TimestampMixin):
    """PM Decision Engine audit — every ENTER/ADD/HOLD/REDUCE/EXIT/
    BLOCK/REVIEW with the full input snapshot and book context
    (spec §27 DecisionAudit)."""

    __tablename__ = "pm_decisions"

    symbol: Mapped[str]
    decision: Mapped[str]                # ENTER|ADD|HOLD|REDUCE|EXIT|BLOCK|REVIEW
    reason: Mapped[str]
    inputs: Mapped[dict] = mapped_column(JSON)   # thesis/valuation/technical/risk/portfolio checks
    equity: Mapped[float | None]
    exposure: Mapped[float | None]
    open_risk: Mapped[float | None]
    leverage: Mapped[float | None]
    rr: Mapped[float | None]
    engine_version: Mapped[str] = mapped_column(default="risk-pyramid/v2.0")
    decided_by: Mapped[str | None]


class TradeIdea(Base, IdMixin, TimestampMixin):
    """Formal trade object (spec §4/§33) — every trade idea becomes a
    persisted record linking research → risk → execution → audit.
    Created by /risk/pretrade; status tracks the gate decision."""

    __tablename__ = "trade_ideas"
    __table_args__ = (Index("ix_trade_ideas_instr", "instrument_id"),)

    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    risk_check_id: Mapped[str | None] = mapped_column(
        ForeignKey("risk_checks.id"))
    strategy: Mapped[str | None]
    direction: Mapped[str] = mapped_column(default="long")  # long|short
    entry_price: Mapped[float]
    stop_price: Mapped[float]
    target_price: Mapped[float | None]
    fair_value: Mapped[float | None]
    proposed_quantity: Mapped[float]
    thesis: Mapped[str | None]
    thesis_status: Mapped[str] = mapped_column(default="REVIEW")
    # INPUT|PASS|REVIEW|BLOCK — the Trade Risk Sheet decision
    status: Mapped[str] = mapped_column(default="REVIEW")
    sheet: Mapped[dict] = mapped_column(JSON, default=dict)
    engine_version: Mapped[str] = mapped_column(default="risk-pyramid/v2.0")
    created_by: Mapped[str | None]


class RiskSnapshot(Base, IdMixin, TimestampMixin):
    """Periodic book-level risk snapshot (spec §33) — equity, exposure,
    leverage, margin, open risk, stress loss, drawdown. Written by
    monitor sweeps so the risk path is auditable over time."""

    __tablename__ = "risk_snapshots"

    equity: Mapped[float]
    cash: Mapped[float | None]
    gross_exposure: Mapped[float | None]
    net_exposure: Mapped[float | None]
    leverage: Mapped[float | None]
    margin_used: Mapped[float | None]
    margin_utilisation: Mapped[float | None]
    open_risk: Mapped[float | None]
    open_risk_pct: Mapped[float | None]
    risk_capacity: Mapped[float | None]
    drawdown_pct: Mapped[float | None]
    var_95: Mapped[float | None]
    stress_loss: Mapped[float | None]
    status: Mapped[str | None]          # NORMAL|WATCH|REDUCE
    engine_version: Mapped[str] = mapped_column(default="risk-pyramid/v2.0")


class LimitConfig(Base, IdMixin, TimestampMixin):
    """Versioned risk-limit config — operator-editable; check_order
    reads the newest version. Every version immutable (audit)."""

    __tablename__ = "limit_configs"

    version: Mapped[int]
    payload: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[str | None]
    note: Mapped[str | None]


class SleeveState(Base, IdMixin, TimestampMixin):
    """Trading-sleeve lifecycle — single-row runtime state (config
    lives in LimitConfig; this is the live ledger). The portfolio
    stop is absolute: at the drawdown threshold the sleeve liquidates
    and enters COOLDOWN; new entries/adds block until a human
    releases it."""

    __tablename__ = "sleeve_state"

    state: Mapped[str] = mapped_column(default="active")  # active|cooldown
    equity_hwm: Mapped[float | None]      # high-water mark, marked eq
    drawdown_pct: Mapped[float | None]    # last observed drawdown
    cooldown_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    cooldown_reason: Mapped[str | None]
    liquidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    released_by: Mapped[str | None]
    release_note: Mapped[str | None]
