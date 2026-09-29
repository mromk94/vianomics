"""Command Center payload schema.

Every section carries `demo: bool`. Demo fixtures are only emitted when
DEMO_FIXTURES=true; otherwise sections return empty collections with
demo=false so the UI renders honest empty states. Provider/system health
is always real (never demo).
"""

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["info", "warning", "critical"]
Direction = Literal["up", "down", "flat"]


class PortfolioSummary(BaseModel):
    total_value: float | None = None
    cash: float | None = None
    daily_pnl: float | None = None
    daily_pnl_pct: float | None = None
    unrealized_pnl: float | None = None
    currency: str = "USD"
    holdings: list[dict] = []   # per-position rows across all books


class PortfolioSplit(BaseModel):
    investment_pct: float | None = None  # mandate target: 70
    trading_pct: float | None = None     # mandate target: 30


class RegimeSnapshot(BaseModel):
    economic_regime: str | None = None   # Recovery/Expansion/Slowdown/Recession
    market_regime: str | None = None     # Risk-on/Neutral/Risk-off
    fear_greed: int | None = None
    vix: float | None = None
    macro_indicators: dict[str, str] = Field(default_factory=dict)


class RiskUtilization(BaseModel):
    drawdown_pct: float | None = None
    drawdown_limit_pct: float = 15.0
    max_sector_pct: float | None = None
    sector_limit_pct: float = 25.0
    max_position_pct: float | None = None
    position_limit_pct: float = 10.0
    var_95: float | None = None
    stress_10pct: float | None = None
    avg_correlation: float | None = None
    warnings: list[str] = Field(default_factory=list)


class WatchlistItem(BaseModel):
    ticker: str
    name: str | None = None
    green_zone_score: int | None = None  # x/20
    last_price: float | None = None
    change_pct: float | None = None


class ApprovalItem(BaseModel):
    id: str
    ticker: str
    action: str
    requested_at: str
    cio_rating: str | None = None
    risk_verdict: str | None = None


class AlertItem(BaseModel):
    id: str
    severity: Severity
    message: str
    source: str
    at: str


class DecisionItem(BaseModel):
    id: str
    ticker: str
    verdict: str
    at: str
    cio_confidence: int | None = None


class ProviderHealth(BaseModel):
    name: str
    status: Literal["up", "down", "degraded", "unconfigured"]
    last_sync: str | None = None
    detail: str | None = None


class CioBlock(BaseModel):
    rating: str | None = None            # STRONG BUY|BUY|HOLD|SELL
    confidence: int | None = None        # 0-100, Part-20 model (not P&L prob)
    expected_return_pct: float | None = None
    mos_pct: float | None = None
    risk_veto: str | None = None         # None|reason — veto is absolute
    kill_conditions: list[str] = Field(default_factory=list)
    conflict_note: str | None = None


class AgentVote(BaseModel):
    agent: str
    recommendation: str                  # BUY|SELL|NEUTRAL|BLOCK…
    score: int | None = None


class SectorPerf(BaseModel):
    sector: str
    weight_pct: float | None = None
    day_pct: float | None = None
    week_pct: float | None = None
    momentum: float | None = None        # 0-100 bar


class CalendarEvent(BaseModel):
    title: str
    at: str
    detail: str | None = None


class TradeSignal(BaseModel):
    kind: str                            # entry|exit|pyramid|risk
    message: str
    danger: bool = False


class Section(BaseModel):
    """A command-center section. `demo=True` means fixture data — the UI
    must label it. Empty/null collections with demo=False are real empty
    states."""

    demo: bool = False


class CommandCenterResponse(BaseModel):
    generated_at: str
    portfolio: PortfolioSummary
    split: PortfolioSplit
    regime: RegimeSnapshot
    risk: RiskUtilization
    cio: CioBlock = Field(default_factory=CioBlock)
    agents: list[AgentVote] = Field(default_factory=list)
    sectors: list[SectorPerf] = Field(default_factory=list)
    calendar: list[CalendarEvent] = Field(default_factory=list)
    signals: list[TradeSignal] = Field(default_factory=list)
    watchlist: list[WatchlistItem] = Field(default_factory=list)
    approvals: list[ApprovalItem] = Field(default_factory=list)
    alerts: list[AlertItem] = Field(default_factory=list)
    decisions: list[DecisionItem] = Field(default_factory=list)
    providers: list[ProviderHealth] = Field(default_factory=list)
    demo_sections: list[str] = Field(default_factory=list)
