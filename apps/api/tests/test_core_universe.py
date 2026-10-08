"""Core-universe (S&P 500 ∪ most-actives) + market-data gap tests."""

from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.models.instruments import (
    Instrument,
    InstrumentIdentifier,
    Sector,
)
from app.models.market import OhlcvBar
from app.models.universe import Universe, UniverseMembership


# ── constituents parsing (no network — fixture HTML) ──

WIKI_FRAGMENT = """
<table id="constituents" class="wikitable sortable">
<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th>
<th>GICS Sub-Industry</th><th>HQ</th><th>Date added</th>
<th>CIK</th><th>Founded</th></tr>
<tr><td><a href="/wiki/3M">MMM</a></td><td>3M</td><td>Industrials</td>
<td>Industrial Conglomerates</td><td>Saint Paul, Minnesota</td>
<td>1957-03-04</td><td>0000066740</td><td>1902</td></tr>
<tr><td><a href="/wiki/Berkshire">BRK.B</a></td><td>Berkshire Hathaway</td>
<td>Financials</td><td>Multi-Sector Holdings</td><td>Omaha</td>
<td>—</td><td>0001067983</td><td>1839</td></tr>
</table>
"""


def test_wiki_table_parser_extracts_constituents():
    from app.providers.constituents import _TableParser
    p = _TableParser()
    p.feed(WIKI_FRAGMENT)
    assert len(p.rows) == 3          # header + 2 data rows
    assert p.rows[1][0] == "MMM"
    assert p.rows[2][6] == "0001067983"


def test_canon_symbol_normalizes_to_dash():
    from app.providers.constituents import canon_symbol
    assert canon_symbol("brk.b") == "BRK-B"
    assert canon_symbol(" AAPL ") == "AAPL"
    assert canon_symbol("BF.B") == "BF-B"


async def test_sp500_constituents_parses(monkeypatch):
    """Live-parse path with fixture HTML — symbol/name/sector/CIK."""
    from app.providers import constituents as cs

    class _Resp:
        text = WIKI_FRAGMENT

        def raise_for_status(self):
            return None

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, **kw):
            return _Resp()

    monkeypatch.setattr(cs.httpx, "AsyncClient",
                        lambda **kw: _Client())
    rows = await cs.sp500_constituents()
    by = {r["symbol"]: r for r in rows}
    assert by["MMM"]["cik"] == "66740"        # zeros stripped
    assert by["MMM"]["sector"] == "Industrials"
    assert "BRK-B" in by                      # dot → dash canonical


# ── the sync itself ──

async def _fake_sources(db):
    rows = [
        {"symbol": "AAA", "name": "Triple A Corp", "sector": "Industrials",
         "sub_industry": "X", "cik": "12345", "index": "sp500"},
        {"symbol": "BRK-B", "name": "Berkshire", "sector": "Financials",
         "sub_industry": "Y", "cik": "1067983", "index": "sp500"},
        {"symbol": "HOT", "name": "HotStock", "sector": None,
         "sub_industry": None, "cik": None, "index": "most_active",
         "asset_class": "equity"},
        {"symbol": "XLK", "name": "Tech SPDR", "sector": None,
         "sub_industry": None, "cik": None, "index": "most_active",
         "asset_class": "etf"},
    ]
    return rows


async def test_sync_core_universe_creates_book(db):
    """Constituents land in global + core with real names, sectors,
    and CIK identifiers — and ETF quoteType maps to asset_class=etf."""
    from app.services import universe as usvc

    fake = await _fake_sources(db)
    with patch("app.providers.constituents.sp500_constituents",
               return_value=fake[:2]), \
         patch("app.providers.constituents.yahoo_most_actives",
               return_value=fake[2:]):
        res = await usvc.sync_core_universe(db, hydrate_batch=0)
    assert res["status"] == "success"
    assert res["constituents"] == 4
    assert res["instruments_created"] == 4

    insts = {i.symbol: i for i in
             (await db.execute(select(Instrument))).scalars().all()}
    assert insts["AAA"].name == "Triple A Corp"
    assert insts["HOT"].asset_class == "equity"
    assert insts["XLK"].asset_class == "etf"

    # sector + CIK landed
    sec = (await db.execute(
        select(Sector).where(Sector.name == "Industrials"))
    ).scalar_one()
    assert insts["AAA"].sector_id == sec.id
    cik = (await db.execute(
        select(InstrumentIdentifier).where(
            InstrumentIdentifier.instrument_id == insts["AAA"].id,
            InstrumentIdentifier.scheme == "cik"))).scalar_one()
    assert cik.value == "12345"

    # memberships: global + core
    for uname in ("global", "core"):
        u = (await db.execute(
            select(Universe).where(Universe.name == uname))
        ).scalar_one()
        n = (await db.execute(
            select(func.count(UniverseMembership.id)).where(
                UniverseMembership.universe_id == u.id,
                UniverseMembership.status == "active"))).scalar()
        assert n == 4

    # idempotent — second sync creates nothing
    with patch("app.providers.constituents.sp500_constituents",
               return_value=fake[:2]), \
         patch("app.providers.constituents.yahoo_most_actives",
               return_value=fake[2:]):
        res2 = await usvc.sync_core_universe(db, hydrate_batch=0)
    assert res2["instruments_created"] == 0
    assert res2["core_members_added"] == 0


async def test_yahoo_bars_never_duplicate_a_session(db):
    """An alpaca bar for a session must block a yahoo same-day row —
    two rows for one trading day corrupts ATR. (The alpaca side
    already dedupes cross-source; the yahoo side now mirrors it.)"""
    from app.ingestion.jobs import ingest_stooq_bars

    inst = Instrument(symbol="DEDUP", name="DupCo")
    db.add(inst)
    await db.flush()
    # an alpaca bar already owns this session
    db.add(OhlcvBar(instrument_id=inst.id, timeframe="1d",
                    time=datetime(2025, 5, 1, 13, 30, tzinfo=UTC),
                    open=10, high=11, low=9, close=10.5, volume=1e6,
                    source="alpaca"))
    await db.commit()

    class FakeYahoo:
        async def fetch_daily(self, symbol, range_="2y", start=None):
            return [{"observed_at": datetime(2025, 5, 1, tzinfo=UTC),
                     "open": 10, "high": 11, "low": 9,
                     "close": 10.5, "volume": 2e6},
                    {"observed_at": datetime(2025, 5, 2, tzinfo=UTC),
                     "open": 10.5, "high": 12, "low": 10,
                     "close": 11.5, "volume": 1e6}]

    run = await ingest_stooq_bars(db, FakeYahoo(), "DEDUP")
    await db.commit()
    assert run.status == "success"
    rows = (await db.execute(
        select(OhlcvBar).where(OhlcvBar.instrument_id == inst.id))
    ).scalars().all()
    by_day = {r.time.date(): r for r in rows}
    assert len(rows) == 2                    # 5/1 not duplicated
    assert by_day[date(2025, 5, 1)].source == "alpaca"
    assert by_day[date(2025, 5, 2)].source == "yahoo"


async def test_tracked_scope_includes_core_members(db):
    """fundamentals_gapfill's 'tracked' scope must see core members —
    they carry zero bars before hydration but ARE tiered."""
    from app.services import universe as usvc

    core = Universe(name="core", tier="core")
    inst = Instrument(symbol="COREM", name="CoreCo",
                      asset_class="equity")
    db.add_all([core, inst])
    await db.flush()
    db.add(UniverseMembership(universe_id=core.id,
                              instrument_id=inst.id, status="active"))
    await db.commit()

    hydrated: list[str] = []

    async def fake_hyd(_db, i):
        hydrated.append(i.symbol)
        return {"bars": True, "facts": True, "market_stats": True}

    with patch.object(usvc, "hydrate_instrument", fake_hyd):
        res = await usvc.fundamentals_gapfill(db, limit=5)
    assert res["checked"] >= 1
    assert "COREM" in hydrated
