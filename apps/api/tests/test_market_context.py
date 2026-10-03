from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.instruments import Instrument
from app.models.market import MarketQuote, OhlcvBar
from app.models.portfolio import ExternalAccount
from app.services import market_context as mc


async def test_context_seed_creates_all_idempotent(db):
    n = await mc.ensure_market_context(db)
    assert n == len(mc.CONTEXT_INSTRUMENTS)
    n2 = await mc.ensure_market_context(db)
    assert n2 == n
    cnt = (await db.execute(
        select(func.count(Instrument.id)))).scalar()
    assert cnt == n
    inst = (await db.execute(
        select(Instrument).where(Instrument.symbol == "^VIX"))
    ).scalar_one()
    assert inst.asset_class == "index"


async def test_update_adv_from_daily_bars(db):
    inst = Instrument(symbol="AAA", name="AAA", asset_class="equity")
    db.add(inst)
    await db.flush()
    for i in range(30):
        db.add(OhlcvBar(
            instrument_id=inst.id, timeframe="1d",
            time=datetime(2026, 9, 1, tzinfo=UTC) + timedelta(days=i),
            open=10, high=11, low=9, close=10, volume=1_000_000,
            source="yahoo"))
    await db.flush()
    n = await mc.update_adv(db)
    assert n == 1
    assert float(inst.avg_dollar_volume_30d) == pytest.approx(1e7)


async def test_portfolio_ctx_marks_to_pushed_bid(db):
    """MT4 positions mark to the pushed bid, not the open price."""
    from app.routers.risk import _portfolio_ctx
    acc = ExternalAccount(
        source="mt4", label="MT4 #1", connected=True,
        equity=10000, balance=8000,
        positions=[{"symbol": "XAUUSD", "qty": 1, "price": 3900.0,
                    "profit": -50.0, "type": "buy",
                    "bid": 3890.0, "ask": 3890.5}])
    db.add(acc)
    await db.flush()
    ctx = await _portfolio_ctx(db)
    assert len(ctx["positions"]) == 1
    p = ctx["positions"][0]
    assert p["current_price"] == 3890.0
    assert p["mark_source"] == "push"
    assert p["market_value"] == pytest.approx(3890.0)


async def test_portfolio_ctx_prefers_fresh_market_quote(db):
    """A fresh market_quotes row beats the snapshot's pushed bid —
    the tape wins over the position payload."""
    from app.routers.risk import _portfolio_ctx
    acc = ExternalAccount(
        source="mt4", label="MT4 #1", connected=True,
        equity=10000, balance=8000,
        positions=[{"symbol": "XAUUSD", "qty": 1, "price": 3900.0,
                    "profit": -50.0, "type": "buy",
                    "bid": 3890.0, "ask": 3890.5}])
    db.add(acc)
    db.add(MarketQuote(
        source="mt4", symbol="XAUUSD", bid=3905.0, ask=3905.6,
        mid=3905.3, ts=datetime.now(UTC)))
    await db.flush()
    ctx = await _portfolio_ctx(db)
    p = ctx["positions"][0]
    assert p["current_price"] == 3905.0
    assert p["mark_source"] == "mt4"
    assert p["spread"] == pytest.approx(0.6, abs=1e-6)


async def test_portfolio_ctx_stale_quote_falls_back(db):
    """A quote older than 30min is not trusted — pushed bid wins."""
    from app.routers.risk import _portfolio_ctx
    acc = ExternalAccount(
        source="mt4", label="MT4 #1", connected=True,
        equity=10000, balance=8000,
        positions=[{"symbol": "XAUUSD", "qty": 1, "price": 3900.0,
                    "profit": -50.0, "type": "buy",
                    "bid": 3890.0, "ask": 3890.5}])
    db.add(acc)
    db.add(MarketQuote(
        source="mt4", symbol="XAUUSD", bid=3905.0, ask=3905.6,
        mid=3905.3, ts=datetime.now(UTC) - timedelta(hours=2)))
    await db.flush()
    ctx = await _portfolio_ctx(db)
    p = ctx["positions"][0]
    assert p["current_price"] == 3890.0
    assert p["mark_source"] == "push"


async def test_short_position_marks_to_ask(db):
    from app.routers.risk import _portfolio_ctx
    acc = ExternalAccount(
        source="mt4", label="MT4 #1", connected=True,
        equity=10000, balance=8000,
        positions=[{"symbol": "EURUSD", "qty": 1, "price": 1.09,
                    "profit": 10.0, "type": "sell",
                    "bid": 1.085, "ask": 1.0854}])
    db.add(acc)
    await db.flush()
    ctx = await _portfolio_ctx(db)
    p = ctx["positions"][0]
    assert p["direction"] == "short"
    assert p["current_price"] == 1.0854   # shorts mark to ask


async def test_open_price_is_last_resort_mark(db):
    """No quote and no pushed bid → open price, flagged 'open'."""
    from app.routers.risk import _portfolio_ctx
    acc = ExternalAccount(
        source="mt4", label="MT4 #1", connected=True,
        equity=10000, balance=8000,
        positions=[{"symbol": "XAUUSD", "qty": 1, "price": 3900.0,
                    "profit": -50.0, "type": "buy"}])
    db.add(acc)
    await db.flush()
    ctx = await _portfolio_ctx(db)
    p = ctx["positions"][0]
    assert p["current_price"] == 3900.0
    assert p["mark_source"] == "open"


async def test_sector_etfs_join_universes_and_sectors(db):
    """Sector SPDRs get GICS sector_id + approved/eligible/global
    memberships — they are tradeable screening-universe members."""
    from app.models.instruments import Sector
    from app.models.universe import Universe, UniverseMembership

    sec = Sector(name="Information Technology")
    db.add(sec)
    for name, tier in (("global", "global"), ("eligible", "eligible"),
                       ("approved", "approved")):
        db.add(Universe(tenant_id="default", name=name, tier=tier))
    await db.flush()

    await mc.ensure_market_context(db)
    xlk = (await db.execute(
        select(Instrument).where(Instrument.symbol == "XLK"))
    ).scalar_one()
    assert xlk.sector_id == sec.id

    memberships = (await db.execute(
        select(Universe.tier)
        .join(UniverseMembership,
              UniverseMembership.universe_id == Universe.id)
        .where(UniverseMembership.instrument_id == xlk.id,
               UniverseMembership.status == "active"))
    ).scalars().all()
    assert set(memberships) == {"global", "eligible", "approved"}

    # macro/index context is known (global) but not tradeable
    spy = (await db.execute(
        select(Instrument).where(Instrument.symbol == "SPY"))
    ).scalar_one()
    spy_tiers = (await db.execute(
        select(Universe.tier)
        .join(UniverseMembership,
              UniverseMembership.universe_id == Universe.id)
        .where(UniverseMembership.instrument_id == spy.id,
               UniverseMembership.status == "active"))
    ).scalars().all()
    assert spy_tiers == ["global"]


async def test_etf_exempt_from_market_cap_gate(db):
    """ETFs have AUM not market cap — the mcap gate must not block
    them; liquidity still applies."""
    from app.services.universe import eligibility_reasons
    etf = Instrument(symbol="XLK", asset_class="etf",
                     listing_status="active", market_cap=None,
                     avg_dollar_volume_30d=1e9)
    assert eligibility_reasons(etf, {
        "asset_classes": ["equity", "etf"],
        "listing_status": ["active"],
        "min_market_cap": 300_000_000,
        "min_avg_dollar_volume": 5_000_000}) == []
    # but thin liquidity still blocks
    etf.avg_dollar_volume_30d = 100
    assert any("adv30" in r for r in eligibility_reasons(etf, {
        "asset_classes": ["equity", "etf"],
        "listing_status": ["active"],
        "min_market_cap": 300_000_000,
        "min_avg_dollar_volume": 5_000_000}))


async def test_tiingo_intraday_ingest(db):
    """Mock Tiingo IEX → 30min bars + a market_quotes row from the
    last close."""
    from app.ingestion.jobs import ingest_tiingo_intraday

    class FakeTiingo:
        async def intraday(self, ticker, start, freq):
            assert freq == "30min"
            return [
                {"date": "2026-09-28T14:30:00.000Z", "open": 100,
                 "high": 101, "low": 99, "close": 100.5,
                 "volume": 1000},
                {"date": "2026-09-28T15:00:00.000Z", "open": 100.5,
                 "high": 102, "low": 100, "close": 101.8,
                 "volume": 1200},
            ]

    db.add(Instrument(symbol="XLK", name="Tech", asset_class="etf"))
    await db.flush()
    run = await ingest_tiingo_intraday(
        db, FakeTiingo(), "XLK", freq="30min")
    assert run.status == "success"
    assert run.records_ok == 2

    bars = (await db.execute(
        select(OhlcvBar).where(OhlcvBar.timeframe == "30min"))
    ).scalars().all()
    assert len(bars) == 2
    assert bars[0].source == "tiingo"

    q = (await db.execute(
        select(MarketQuote).where(MarketQuote.source == "tiingo"))
    ).scalar_one()
    assert q.symbol == "XLK"
    assert float(q.mid) == pytest.approx(101.8)

    # idempotent — second run adds no duplicates
    await ingest_tiingo_intraday(db, FakeTiingo(), "XLK", freq="30min")
    cnt = (await db.execute(
        select(func.count(OhlcvBar.id))
        .where(OhlcvBar.timeframe == "30min"))).scalar()
    assert cnt == 2
