"""Explicitly-labeled demonstration fixtures for unpopulated sections.

These are only served when DEMO_FIXTURES=true and are always flagged via
the `demo_sections` list in the response. They must never be presented to
the user as live brokerage data. When real engines land (M1+), these
fixtures are replaced by queries against the persistence layer.
"""

from app.schemas.command_center import (
    AgentVote,
    AlertItem,
    ApprovalItem,
    CalendarEvent,
    CioBlock,
    DecisionItem,
    PortfolioSplit,
    PortfolioSummary,
    RegimeSnapshot,
    RiskUtilization,
    SectorPerf,
    TradeSignal,
    WatchlistItem,
)

DEMO_PORTFOLIO = PortfolioSummary(
    total_value=1_284_550.00,
    cash=96_300.00,
    daily_pnl=4_812.30,
    daily_pnl_pct=0.38,
    unrealized_pnl=142_900.00,
)

DEMO_SPLIT = PortfolioSplit(investment_pct=68.4, trading_pct=31.6)

DEMO_REGIME = RegimeSnapshot(
    economic_regime="Expansion",
    market_regime="Risk-On",
    fear_greed=31,
    vix=18.5,
    macro_indicators={
        "GDP": "3.2%",
        "CPI": "2.8%",
        "PMI": "52.4",
        "Unemployment": "3.6%",
        "Fed Funds": "5.50%",
        "Yield Curve": "-0.35%",
    },
)

DEMO_RISK = RiskUtilization(
    drawdown_pct=3.2,
    max_sector_pct=46.8,
    max_position_pct=12.0,
    var_95=-2450.0,
    stress_10pct=-8900.0,
    avg_correlation=0.42,
    warnings=[
        "Technology sector at 46.8% exceeds 25% limit",
        "PLTR position at 12.0% exceeds 10% single-stock limit",
    ],
)

DEMO_CIO = CioBlock(
    rating="BUY",
    confidence=82,
    expected_return_pct=18.0,
    mos_pct=32.0,
    risk_veto=None,
    kill_conditions=["PLTR >10% (12.0%)"],
    conflict_note="Conflict: Technical Agent SELL vs. consensus. Risk Manager Veto: None.",
)

DEMO_AGENTS = [
    AgentVote(agent="Fundamental", recommendation="BUY", score=88),
    AgentVote(agent="Valuation", recommendation="BUY", score=82),
    AgentVote(agent="Rule #1", recommendation="BUY", score=91),
    AgentVote(agent="Quant", recommendation="BUY", score=76),
    AgentVote(agent="Macro", recommendation="NEUTRAL", score=55),
    AgentVote(agent="Technical", recommendation="SELL", score=34),
    AgentVote(agent="Risk", recommendation="BUY", score=80),
    AgentVote(agent="Portfolio", recommendation="BUY", score=78),
    AgentVote(agent="CIO (Synthesis)", recommendation="BUY", score=82),
]

DEMO_SECTORS = [
    SectorPerf(sector="Technology", weight_pct=46.8, day_pct=1.2, week_pct=3.8, momentum=80),
    SectorPerf(sector="Comm. Services", weight_pct=12.9, day_pct=0.7, week_pct=2.1, momentum=65),
    SectorPerf(sector="Consumer Cyclical", weight_pct=15.4, day_pct=0.4, week_pct=-0.3, momentum=40),
    SectorPerf(sector="Financials", weight_pct=9.9, day_pct=-0.2, week_pct=0.9, momentum=55),
    SectorPerf(sector="Healthcare", weight_pct=9.8, day_pct=0.3, week_pct=1.5, momentum=60),
    SectorPerf(sector="Industrials", weight_pct=4.1, day_pct=-0.5, week_pct=-1.2, momentum=30),
    SectorPerf(sector="Energy", weight_pct=0.0, day_pct=-1.1, week_pct=-4.2, momentum=15),
]

DEMO_CALENDAR = [
    CalendarEvent(title="Monthly Employment Report", at="2026-10-02", detail="08:20 ET"),
    CalendarEvent(title="CPI [Inflation] Report", at="2026-10-13", detail="08:20 ET"),
    CalendarEvent(title="FOMC Meeting", at="2026-10-27", detail="Day 1-2"),
    CalendarEvent(title="GDP Third Estimate", at="2026-10-29", detail="08:20 ET"),
]

DEMO_SIGNALS = [
    TradeSignal(kind="entry", message="Entry: Technical Mean Reversion triggered on AAPL (RSI 28)"),
    TradeSignal(kind="exit", message="Exit: Fundamental — PEP Green Zone dropped to 14/20", danger=True),
    TradeSignal(kind="pyramid", message="Pyramid: PLTR in STATE 3 — Target 1 hit, position doubled"),
    TradeSignal(kind="risk", message="Risk: NVDA stop at $630.20 (1.5× ATR)", danger=True),
]

DEMO_WATCHLIST = [
    WatchlistItem(
        ticker="NVDA", name="NVIDIA", green_zone_score=19,
        last_price=875.40, change_pct=1.24,
    ),
    WatchlistItem(
        ticker="AAPL", name="Apple", green_zone_score=16,
        last_price=228.10, change_pct=-0.42,
    ),
    WatchlistItem(
        ticker="PLTR", name="Palantir", green_zone_score=15,
        last_price=42.75, change_pct=2.10,
    ),
]

DEMO_APPROVALS = [
    ApprovalItem(
        id="apr-001",
        ticker="NVDA",
        action="BUY — initial position",
        requested_at="2026-09-26T14:32:00Z",
        cio_rating="BUY",
        risk_verdict="PASS",
    )
]

DEMO_ALERTS = [
    AlertItem(
        id="al-001",
        severity="warning",
        message="PEP Green Zone score dropped to 14/20",
        source="monitoring",
        at="2026-09-26T09:15:00Z",
    ),
    AlertItem(
        id="al-002",
        severity="info",
        message="PLTR pyramid in STATE 3 — Target 1 hit, position doubled",
        source="pyramid",
        at="2026-09-25T16:02:00Z",
    ),
]

DEMO_DECISIONS = [
    DecisionItem(
        id="dec-001",
        ticker="NVDA",
        verdict="APPROVED",
        at="2026-09-24T18:40:00Z",
        cio_confidence=82,
    ),
    DecisionItem(
        id="dec-002",
        ticker="TSLA",
        verdict="WATCHLIST",
        at="2026-09-23T15:10:00Z",
        cio_confidence=54,
    ),
]
