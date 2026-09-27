"""Explicitly-labeled demonstration fixtures for unpopulated sections.

These are only served when DEMO_FIXTURES=true and are always flagged via
the `demo_sections` list in the response. They must never be presented to
the user as live brokerage data. When real engines land (M1+), these
fixtures are replaced by queries against the persistence layer.
"""

from app.schemas.command_center import (
    AlertItem,
    ApprovalItem,
    DecisionItem,
    PortfolioSplit,
    PortfolioSummary,
    RegimeSnapshot,
    RiskUtilization,
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
)

DEMO_RISK = RiskUtilization(
    drawdown_pct=3.2,
    max_sector_pct=46.8,
    max_position_pct=12.0,
    warnings=[
        "Technology sector at 46.8% exceeds 25% limit",
        "PLTR position at 12.0% exceeds 10% single-stock limit",
    ],
)

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
