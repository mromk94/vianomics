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
