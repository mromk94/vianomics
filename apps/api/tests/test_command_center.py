from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

client = TestClient(app)


def test_command_center_shape() -> None:
    r = client.get("/api/v1/command-center")
    assert r.status_code == 200
    body = r.json()
    for key in (
        "generated_at", "portfolio", "split", "regime", "risk",
        "watchlist", "approvals", "alerts", "decisions",
        "providers", "demo_sections",
    ):
        assert key in body


def test_command_center_demo_flagging() -> None:
    r = client.get("/api/v1/command-center")
    body = r.json()
    if get_settings().demo_fixtures:
        # Demo sections must be explicitly flagged — never implied live.
        assert "portfolio" in body["demo_sections"]
        assert body["portfolio"]["total_value"] is not None
    else:
        assert body["demo_sections"] == []
        assert body["portfolio"]["total_value"] is None


def test_provider_health_is_never_demo() -> None:
    r = client.get("/api/v1/command-center")
    body = r.json()
    names = {p["name"] for p in body["providers"]}
    assert {"PostgreSQL", "Redis"} <= names
    assert "providers" not in body["demo_sections"]


async def test_internal_book_excludes_external_cash(db) -> None:
    """Ghost-balance regression — the internal view must show the
    ledger book only: external broker balances (MT4/IBKR syncs) must
    not inflate its cash, and `source=all` must count each book once.
    Internal equity = ledger cash + position market values."""
    from app.models.instruments import Instrument
    from app.models.macro import RegimeRun
    from app.models.market import OhlcvBar
    from app.models.portfolio import (
        ExternalAccount, LedgerEntry, Portfolio, Position)
    from app.routers.command_center import command_center
    from app.routers.risk import _portfolio_ctx
    from app.services import cache

    pf = Portfolio(name="paper", broker="paper")
    inst = Instrument(symbol="TST", name="Test Co")
    db.add_all([pf, inst])
    await db.flush()
    db.add(LedgerEntry(portfolio_id=pf.id, kind="deposit",
                       amount=100_000))
    db.add(Position(portfolio_id=pf.id, instrument_id=inst.id,
                    quantity=10, avg_cost=100))
    db.add(OhlcvBar(instrument_id=inst.id, timeframe="1d",
                    time=datetime(2025, 6, 2, tzinfo=UTC),
                    open=240, high=255, low=238, close=250,
                    source="seed"))
    # a persisted regime run keeps the endpoint off live macro fetch
    db.add(RegimeRun(as_of=datetime(2025, 6, 2, tzinfo=UTC),
                     rules_version="t", econ_regime="expansion",
                     market_regime="neutral", fear_greed=50, vix=15.0,
                     features={}, rule_hits=[]))
    db.add(ExternalAccount(
        source="mt4", label="MT4 #1", currency="USD",
        balance=300_000, equity=305_000, connected=True,
        positions=[], equity_history=[]))
    await db.commit()

    ctx = await _portfolio_ctx(db)
    assert ctx["has_book"]
    assert ctx["cash_internal"] == 100_000
    assert ctx["nav_internal"] == 102_500      # 100k cash + 10 × $250
    # combined display figures remain available, labeled as such
    assert ctx["cash"] == 400_000
    assert ctx["nav"] == 407_500

    cache.invalidate()
    internal = await command_center("internal", db)
    assert internal.portfolio.cash == 100_000
    assert internal.portfolio.total_value == 102_500

    cache.invalidate()
    combined = await command_center("all", db)
    assert combined.portfolio.cash == 400_000      # ext cash once
    assert combined.portfolio.total_value == 407_500
