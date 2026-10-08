"""Alpaca data/broker adapters + broker account sync + IBKR Flex —
all HTTP mocked, no network."""

import httpx
import pytest
from datetime import datetime, timedelta, UTC
from sqlalchemy import select

from app.models.instruments import Instrument
from app.models.market import MarketQuote, OhlcvBar
from app.models.portfolio import ExternalAccount
from app.providers.alpaca import AlpacaAdapter
from app.providers.broker import AlpacaAdapter as AlpacaBroker
from app.services import broker_sync


def mock_client(payload, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
async def inst(db):
    i = Instrument(symbol="NVDA", name="NVIDIA", asset_class="equity")
    db.add(i)
    await db.flush()
    return i


# ── data adapter ──

async def test_alpaca_bars_ingest(db, inst):
    from app.ingestion.jobs import ingest_alpaca_bars
    payload = {"bars": [
        {"t": "2026-09-28T04:00:00Z", "o": 100, "h": 102, "l": 99,
         "c": 101.5, "v": 1000, "n": 10, "vw": 100.8},
        {"t": "2026-09-29T04:00:00Z", "o": 101.5, "h": 103, "l": 101,
         "c": 102.2, "v": 1100, "n": 12, "vw": 102.0}],
        "next_page_token": None}
    ad = AlpacaAdapter(api_key="k", secret_key="s",
                       client=mock_client(payload))
    run = await ingest_alpaca_bars(db, ad, "NVDA")
    assert run.status == "success" and run.records_ok == 2
    bars = (await db.execute(
        select(OhlcvBar).where(OhlcvBar.source == "alpaca"))
    ).scalars().all()
    # canonical timeframe — '1Day' lands as '1d' so engines see it
    assert len(bars) == 2 and bars[0].timeframe == "1d"
    assert bars[0].close == pytest.approx(101.5)


async def test_alpaca_bars_never_duplicate_a_session(db, inst):
    """A day already covered by another source (yahoo) must not get a
    second '1d' row from alpaca — dupes corrupt ATR/returns."""
    from app.ingestion.jobs import ingest_alpaca_bars
    from app.db.base import utcnow
    db.add(OhlcvBar(instrument_id=inst.id, timeframe="1d",
                    time=utcnow() - timedelta(days=2),
                    open=1, high=2, low=1, close=1.5,
                    volume=100, source="yahoo"))
    await db.commit()
    same_day = (utcnow() - timedelta(days=2)).strftime(
        "%Y-%m-%dT04:00:00Z")
    payload = {"bars": [
        {"t": same_day, "o": 1, "h": 2, "l": 1, "c": 1.6, "v": 5},
        {"t": (utcnow() - timedelta(days=1)).strftime(
            "%Y-%m-%dT04:00:00Z"), "o": 2, "h": 3, "l": 2, "c": 2.5,
         "v": 6}],
        "next_page_token": None}
    ad = AlpacaAdapter(api_key="k", secret_key="s",
                       client=mock_client(payload))
    run = await ingest_alpaca_bars(db, ad, "NVDA")
    assert run.status == "success"
    bars = (await db.execute(
        select(OhlcvBar).where(OhlcvBar.instrument_id == inst.id,
                               OhlcvBar.timeframe == "1d"))
    ).scalars().all()
    assert len(bars) == 2   # one per session — no dupe
    assert {b.source for b in bars} == {"yahoo", "alpaca"}


async def test_alpaca_unconfigured(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    ad = AlpacaAdapter()
    assert ad.capabilities()["configured"] is False


async def test_alpaca_quote_refresh(db, inst, monkeypatch):
    from app.services.market_context import refresh_alpaca_quotes

    class FakeAdapter:
        async def snapshot(self, sym):
            return {"latestQuote": {"bp": 100.0, "ap": 100.2},
                    "latestTrade": {"p": 100.1},
                    "dailyBar": {"o": 99.5},
                    "prevDailyBar": {"c": 99.0}}

    monkeypatch.setattr(
        "app.providers.alpaca.AlpacaAdapter", lambda **kw: FakeAdapter())
    n = await refresh_alpaca_quotes(db, ["NVDA", "^VIX"])
    assert n == ["NVDA"]  # index skipped — stocks/ETFs only
    q = (await db.execute(
        select(MarketQuote).where(MarketQuote.source == "alpaca"))
    ).scalar_one()
    assert float(q.bid) == 100.0 and float(q.ask) == 100.2
    assert float(q.mid) == pytest.approx(100.1)
    assert float(q.day_open) == 99.5 and float(q.prev_close) == 99.0
    assert q.instrument_id == inst.id


# ── broker adapter ──

def test_alpaca_order_map():
    import os
    os.environ["ALPACA_API_KEY"] = "k"
    os.environ["ALPACA_SECRET_KEY"] = "s"
    ad = AlpacaBroker()
    assert ad.live is False  # paper URL → simulated lane
    o = ad._map_order({
        "id": "abc", "symbol": "NVDA", "side": "buy", "qty": "10",
        "type": "limit", "limit_price": "100.5",
        "status": "partially_filled", "filled_qty": "4",
        "filled_avg_price": "100.4"})
    assert o.status == "partially_filled"
    assert o.filled_qty == 4 and o.avg_fill_price == 100.4
    o2 = ad._map_order({"id": "x", "status": "done_for_day", "qty": "1"})
    assert o2.status == "cancelled"


# ── account sync ──

async def test_alpaca_account_sync(db, inst, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/v2/account"):
            return httpx.Response(200, json={
                "equity": "105000.00", "cash": "80000.00",
                "account_number": "PA1234567", "currency": "USD"})
        if url.endswith("/v2/positions"):
            return httpx.Response(200, json=[{
                "symbol": "NVDA", "qty": "10", "avg_entry_price": "90",
                "current_price": "100", "market_value": "1000",
                "unrealized_pl": "100", "side": "long"}])
        if "snapshot" in url:
            return httpx.Response(200, json={
                "latestQuote": {"bp": 99.9, "ap": 100.1},
                "latestTrade": {"p": 100.0},
                "dailyBar": {"o": 99.0}, "prevDailyBar": {"c": 98.5}})
        return httpx.Response(404, json={})

    RealClient = httpx.AsyncClient  # capture before module-level patch

    class FakeClient:
        def __init__(self, *a, **kw):
            self._c = RealClient(
                transport=httpx.MockTransport(handler))
        async def __aenter__(self):
            return self._c
        async def __aexit__(self, *a):
            await self._c.aclose()

    monkeypatch.setattr(broker_sync.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        "app.providers.alpaca.AlpacaAdapter",
        lambda **kw: type("F", (), {"snapshot": staticmethod(
            lambda s: _snap())}))
    async def _snap():
        return {"latestQuote": {"bp": 99.9, "ap": 100.1},
                "latestTrade": {"p": 100.0},
                "dailyBar": {"o": 99.0}, "prevDailyBar": {"c": 98.5}}

    res = await broker_sync.sync_alpaca_account(db)
    assert res["status"] == "success" and res["positions"] == 1
    acc = (await db.execute(
        select(ExternalAccount).where(
            ExternalAccount.source == "alpaca"))).scalar_one()
    assert acc.equity == 105000.0 and acc.connected
    assert acc.positions[0]["symbol"] == "NVDA"
    assert acc.positions[0]["profit"] == 100.0
    assert len(acc.equity_history) == 1


# ── IBKR flex ──

FLEX_STMT = """<?xml version="1.0"?>
<FlexQueryResponse queryName="Test" type="AF">
 <FlexStatements count="1">
  <FlexStatement accountId="U123456" fromDate="2026-09-01" toDate="2026-10-01">
   <OpenPositions>
    <OpenPosition symbol="NVDA" position="50" costBasisPrice="180"
     markPrice="200" positionValue="10000"
     fifoPnlUnrealized="1000" currency="USD"/>
    <OpenPosition symbol="MSFT" position="-10" costBasisPrice="400"
     markPrice="390" positionValue="-3900"
     fifoPnlUnrealized="100" currency="USD"/>
   </OpenPositions>
   <CashReport>
    <CashReportCurrency currency="USD" endingCash="50000"/>
   </CashReport>
   <EquitySummaryInBase>
    <EquitySummaryByReportDateInBase total="60100"/>
   </EquitySummaryInBase>
  </FlexStatement>
 </FlexStatements>
</FlexQueryResponse>"""


async def test_ibkr_flex_sync(db, inst, monkeypatch):
    monkeypatch.setenv("IBKR_FLEX_TOKEN", "tok")
    monkeypatch.setenv("IBKR_FLEX_QUERY_ID", "12345")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "SendRequest" in str(request.url):
            return httpx.Response(200, text=(
                '<?xml version="1.0"?><FlexStatementResponse>'
                "<Status>Success</Status>"
                "<ReferenceCode>ref1</ReferenceCode>"
                "</FlexStatementResponse>"))
        if "GetStatement" in str(request.url):
            calls["n"] += 1
            return httpx.Response(200, text=FLEX_STMT)
        return httpx.Response(404, text="")

    RealClient = httpx.AsyncClient

    class FakeClient:
        def __init__(self, *a, **kw):
            self._c = RealClient(
                transport=httpx.MockTransport(handler))
        async def __aenter__(self):
            return self._c
        async def __aexit__(self, *a):
            await self._c.aclose()

    monkeypatch.setattr(broker_sync.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr("asyncio.sleep",
                        lambda t: _nosleep())
    async def _nosleep():
        return None

    res = await broker_sync.sync_ibkr_flex(db)
    assert res["status"] == "success" and res["positions"] == 2
    acc = (await db.execute(
        select(ExternalAccount).where(
            ExternalAccount.source == "ibkr"))).scalar_one()
    assert acc.label == "IBKR #U123456"
    assert acc.equity == 60100.0 and acc.balance == 50000.0
    pos = {p["symbol"]: p for p in acc.positions}
    assert pos["NVDA"]["qty"] == 50 and pos["NVDA"]["profit"] == 1000
    assert pos["MSFT"]["side"] == "short"


async def test_ibkr_flex_unconfigured(db, monkeypatch):
    monkeypatch.delenv("IBKR_FLEX_TOKEN", raising=False)
    monkeypatch.delenv("IBKR_FLEX_QUERY_ID", raising=False)
    res = await broker_sync.sync_ibkr_flex(db)
    assert res["status"] == "skipped"
