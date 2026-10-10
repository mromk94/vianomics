"""Data-coverage gapfix tests — cross-taxonomy fundamentals (IFRS),
the Yahoo fallback ingest, currency-mismatch guards, and lazy
hydration of uncovered universe members."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.fundamentals import FundamentalObservation
from app.models.instruments import Instrument
from app.models.market import OhlcvBar
from app.models.screening import ScreeningPolicy
from app.services.green_zone import POLICY_DEFAULTS, run_screen, screen_instrument

ASOF = datetime(2025, 6, 1, tzinfo=UTC)


def _obs(inst_id, concept, value, period_end, currency="USD",
         unit=None, source="edgar", fiscal="FY", duration_days=364):
    return FundamentalObservation(
        instrument_id=inst_id, concept=concept,
        value=Decimal(str(value)),
        unit=unit or currency or "USD",
        currency=currency,
        period_start=(period_end - timedelta(days=duration_days)
                      if duration_days else None),
        period_end=period_end, fiscal_period=fiscal,
        observed_at=datetime(period_end.year, period_end.month,
                             period_end.day, tzinfo=UTC),
        published_at=datetime(period_end.year, period_end.month,
                              period_end.day, tzinfo=UTC) + timedelta(days=60),
        source=source,
    )


def _tsm_like(db, inst, currency="TWD"):
    """IFRS-filer fixture — the concepts TSM actually publishes to the
    SEC under the ifrs-full taxonomy (verified against companyfacts)."""
    # 11 FY years (2015→2025, ~11%/yr revenue) — the 10Y CAGR growth
    # criteria need a full decade of history to pass, not a stub
    fy = [(2015 + i, 1.0e12 * 1.112 ** i) for i in range(11)]
    for i, (y, v) in enumerate(fy):
        pe = date(y, 12, 31)
        rows = [
            ("ifrs-full:Revenue", v),
            ("ifrs-full:ProfitLoss", v * 0.40),
            ("ifrs-full:CashFlowsFromUsedInOperatingActivities", v * 0.62),
            ("ifrs-full:PurchaseOfPropertyPlantAndEquipment"
             "ClassifiedAsInvestingActivities", v * 0.35),
            ("ifrs-full:AdjustedWeightedAverageShares", 5.19e9),
            ("ifrs-full:BasicEarningsLossPerShare", 220 + i * 50),
            ("ifrs-full:DividendsPaidOrdinarySharesPerShare",
             10 + i * 1.5),
            ("ifrs-full:CurrentTradeReceivables", 2.0e11 * (1 + i * 0.05)),
            ("ifrs-full:ProfitLossBeforeTax", v * 0.55),
            ("ifrs-full:CurrentTaxExpenseIncome", v * 0.10),
            ("ifrs-full:DepreciationExpense", v * 0.20),
        ]
        for concept, val in rows:
            db.add(_obs(inst.id, concept, val, pe, currency=currency))
    # instant (balance-sheet) facts — no duration; period_end 2025-01-31
    # so published_at lands before ASOF (point-in-time filter)
    for concept, val in [
            ("ifrs-full:Equity", 4.0e12),
            ("ifrs-full:LongtermBorrowings", 9.0e11),
            ("ifrs-full:CurrentPortionOfLongtermBorrowings", 1.4e11),
            ("ifrs-full:CashAndCashEquivalents", 1.9e12),
            ("ifrs-full:CurrentAssets", 3.8e12),
            ("ifrs-full:CurrentLiabilities", 1.5e12),
            ("ifrs-full:FinanceCosts", 5.0e9)]:
        db.add(_obs(inst.id, concept, val, date(2025, 1, 31),
                    currency=currency, duration_days=0))


async def test_ifrs_filer_screens_real_not_no_data(db):
    """TSM files ifrs-full XBRL — the facts are ingested but every
    concept list was us-gaap-only → the screen saw 'no data' despite
    the data sitting in the DB. Now the aliases read them."""
    inst = Instrument(symbol="TSM", name="TSMC", currency="USD")
    db.add(inst)
    await db.flush()
    _tsm_like(db, inst)
    await db.commit()

    res = await screen_instrument(db, inst, POLICY_DEFAULTS, None, ASOF)
    keys = {c["key"]: c for c in res["criteria"]}
    # growth criteria now compute 10Y CAGR from ifrs-full observations
    assert keys["revenue_growth"]["status"] == "pass"
    assert keys["revenue_growth"]["evidence"]["cagr"] > 0.1
    assert keys["revenue_growth"]["evidence"]["years"] >= 9
    assert keys["net_income_growth"]["status"] == "pass"
    assert keys["ocf_growth"]["status"] == "pass"
    assert keys["roe"]["status"] == "pass"           # NI/equity ~29%
    assert keys["cash_conversion"]["status"] == "pass"  # OCF/NI ~1.55
    assert keys["share_count"]["status"] == "pass"   # flat shares
    assert keys["fcf_per_share"]["status"] == "pass"
    assert keys["bvps"]["status"] == "pass"
    assert keys["current_ratio"]["status"] == "pass"  # 2.53
    assert keys["dividend_growth"]["status"] == "pass"
    assert keys["dscr"]["status"] == "pass"
    # currency mismatch: fundamentals TWD vs USD ADR price —
    # price-linked criteria stay insufficient, never a wrong number
    assert keys["p_fcf"]["status"] == "insufficient_data"
    assert keys["iv_discount"]["status"] == "insufficient_data"
    assert "currency_mismatch" in keys["p_fcf"]["evidence"]
    assert res["fundamental_currency"] == "TWD"
    assert res["currency_mismatch"] is True
    # EBIT genuinely absent from TSM's ifrs-full filing → honest gap
    assert keys["margin_expansion"]["status"] == "insufficient_data"


async def test_usd_filer_price_criteria_evaluate(db):
    """Same structure in USD — p_fcf/iv_discount compute normally when
    reporting currency matches the listing."""
    inst = Instrument(symbol="USCO", name="US Co", currency="USD")
    db.add(inst)
    await db.flush()
    _tsm_like(db, inst, currency="USD")
    await db.commit()
    res = await screen_instrument(
        db, inst, POLICY_DEFAULTS, None, ASOF,
        price=50.0, intrinsic_value=200.0)
    keys = {c["key"]: c for c in res["criteria"]}
    assert keys["p_fcf"]["status"] in ("pass", "fail")
    assert keys["iv_discount"]["status"] in ("pass", "fail")
    assert res["currency_mismatch"] is False


async def test_yahoo_fundamentals_ingest(db):
    """The no-SEC-coverage bridge: yahoo timeseries → canonical
    yahoo: concepts the screen reads, with sign normalization
    (Yahoo reports capex negative; engines expect positive outflows)."""
    from app.ingestion.jobs import ingest_yahoo_fundamentals

    class FakeYahoo:
        async def fundamentals_timeseries(self, symbol, types=None,
                                          years=10):
            return {
                "annualTotalRevenue": {
                    "value": 1.0e9, "asOfDate": "2025-01-31",
                    "periodType": "12M", "currencyCode": "USD"},
                "annualTotalRevenue__series": [
                    {"value": 8.0e8, "asOfDate": "2024-01-31",
                     "periodType": "12M", "currencyCode": "USD"},
                    {"value": 1.0e9, "asOfDate": "2025-01-31",
                     "periodType": "12M", "currencyCode": "USD"}],
                "annualCapitalExpenditure": {
                    "value": -5.0e7, "asOfDate": "2025-01-31",
                    "periodType": "12M", "currencyCode": "USD"},
                "annualCapitalExpenditure__series": [
                    {"value": -5.0e7, "asOfDate": "2025-01-31",
                     "periodType": "12M", "currencyCode": "USD"}],
                "trailingMarketCap": {
                    "value": 5.0e9, "asOfDate": "2025-05-30",
                    "periodType": None, "currencyCode": "USD"},
                "trailingMarketCap__series": [
                    {"value": 5.0e9, "asOfDate": "2025-05-30",
                     "periodType": None, "currencyCode": "USD"}],
            }

    inst = Instrument(symbol="YFTEST", name="YahooOnly")
    db.add(inst)
    await db.flush()

    run = await ingest_yahoo_fundamentals(db, FakeYahoo(), "YFTEST")
    await db.commit()
    assert run.status == "success"
    assert run.records_ok == 4          # 2 revenue + 1 capex + 1 mcap
    obs = (await db.execute(
        select(FundamentalObservation).where(
            FundamentalObservation.instrument_id == inst.id))
    ).scalars().all()
    by_concept = {o.concept: o for o in obs}
    assert "yahoo:TotalRevenue" in by_concept
    assert "yahoo:TrailingMarketCap" in by_concept
    # capex stored positive (us-gaap outflow convention)
    assert float(by_concept["yahoo:CapitalExpenditure"].value) == 5.0e7
    # FY duration derived so fy_series accepts it
    rev = by_concept["yahoo:TotalRevenue"]
    assert rev.fiscal_period == "FY"
    assert rev.source == "yahoo"

    # idempotent — a second ingest adds nothing
    run2 = await ingest_yahoo_fundamentals(db, FakeYahoo(), "YFTEST")
    await db.commit()
    assert run2.records_ok == 0

    # the screen reads the yahoo concepts — revenue growth computes on
    # them. 2 FY points is a 1y span → 'review' (partial history), not
    # a 10Y pass and never 'insufficient'
    res = await screen_instrument(
        db, inst, POLICY_DEFAULTS, None, ASOF)
    keys = {c["key"]: c for c in res["criteria"]}
    assert keys["revenue_growth"]["status"] == "review"
    assert keys["revenue_growth"]["evidence"]["cagr"] > 0


async def test_stockrow_fundamentals_ingest(db):
    """StockRow annual metric history → stockrow:-concept observations.
    10y of revenue lands as FY rows the screen's CAGR reads; capex
    arrives negative → stored positive (us-gaap outflow convention);
    a second run dedupes on the deterministic published_at stamp."""
    from app.ingestion.jobs import ingest_stockrow_fundamentals

    class FakeStockRow:
        api_key = "x"

        async def annual_series(self, ticker, slug, limit=12):
            if slug == "revenue":
                return {date(2015 + i, 1, 31): 4.0e8 * 1.12 ** i
                        for i in range(11)}
            if slug == "capex":
                return {date(2024, 1, 31): -5.0e7,
                        date(2025, 1, 31): -6.0e7}
            if slug == "equityt":
                return {date(2025, 1, 31): 5.0e8}
            return {}

    inst = Instrument(symbol="SROW", name="StockRowCo")
    db.add(inst)
    await db.flush()

    run = await ingest_stockrow_fundamentals(db, FakeStockRow(), "SROW")
    await db.commit()
    assert run.status == "success"
    assert run.records_ok == 14          # 11 rev + 2 capex + 1 equity

    obs = (await db.execute(
        select(FundamentalObservation).where(
            FundamentalObservation.instrument_id == inst.id))
    ).scalars().all()
    by_concept = {}
    for o in obs:
        by_concept.setdefault(o.concept, []).append(o)
    rev = by_concept["stockrow:Revenue"]
    assert len(rev) == 11
    assert all(o.source == "stockrow" for o in rev)
    assert all(o.fiscal_period == "FY" for o in rev)
    assert all(o.source_ref == "stockrow:revenue" for o in rev)
    # outflow convention — negative capex stored positive
    cap = by_concept["stockrow:CapitalExpenditures"]
    assert all(float(o.value) > 0 for o in cap)
    # instant rows carry no period_start but stay fy_series-visible
    eq = by_concept["stockrow:StockholdersEquity"][0]
    assert eq.period_start is None and eq.fiscal_period == "FY"

    run2 = await ingest_stockrow_fundamentals(
        db, FakeStockRow(), "SROW")
    await db.commit()
    assert run2.records_ok == 0          # idempotent

    # the stockrow: aliases feed fy_series → the screen's 10Y CAGR
    res = await screen_instrument(db, inst, POLICY_DEFAULTS, None, ASOF)
    keys = {c["key"]: c for c in res["criteria"]}
    assert keys["revenue_growth"]["status"] == "pass"
    assert keys["revenue_growth"]["evidence"]["years"] >= 9


async def test_ensure_instrument_hydrates_uncovered_existing(db):
    """A security-master row that exists but has zero coverage (e.g.
    an Alpaca-pool member) must hydrate on demand — not return
    'already known' and screen empty."""
    from app.services import universe as uni_svc
    from unittest.mock import AsyncMock, patch

    inst = Instrument(symbol="POOLX", name="PoolCo",
                      asset_class="equity", currency="USD")
    db.add(inst)
    await db.commit()

    called = {"n": 0}

    async def fake_hydrate(_db, _inst):
        called["n"] += 1
        return {"bars": True, "facts": True, "market_stats": True}

    with patch.object(uni_svc, "hydrate_instrument", fake_hydrate):
        res = await uni_svc.ensure_instrument(db, "POOLX")
    assert res["created"] is False
    assert called["n"] == 1            # hydration ran
    assert res["fundamentals"] is True

    # second call — still uncovered in the fake world? coverage_missing
    # is real and still sees zero bars/facts → hydrates again (fine)
    # but the POINT: it never silently returns unhydrated
    with patch.object(uni_svc, "hydrate_instrument", fake_hydrate):
        res2 = await uni_svc.ensure_instrument(db, "POOLX")
    assert called["n"] == 2


async def test_run_screen_lazy_hydration(db):
    """hydrate=True pulls coverage for uncovered members before
    screening them — the Alpaca-pool 'wall of NO DATA' fix."""
    from app.models.screening import ScreeningResult
    from app.models.universe import Universe, UniverseMembership
    from unittest.mock import patch

    u = Universe(name="global", tier="global")
    inst = Instrument(symbol="NOPOOL", name="NoCoverage")
    db.add_all([u, inst])
    await db.flush()
    db.add(UniverseMembership(universe_id=u.id, instrument_id=inst.id,
                              status="active"))
    policy = ScreeningPolicy(version=1, is_active=True,
                             params=POLICY_DEFAULTS)
    db.add(policy)
    await db.flush()

    hydrated: list[str] = []

    async def fake_hydrate(_db, i):
        hydrated.append(i.symbol)
        # simulate a real hydration — add a bar so technical_setup
        # has something to read
        db.add(OhlcvBar(instrument_id=i.id, timeframe="1d", time=ASOF,
                        open=10, high=10, low=10, close=10, volume=1e6,
                        source="yahoo"))
        await db.flush()
        return {"bars": True, "facts": False, "market_stats": False}

    with patch("app.services.universe.hydrate_instrument", fake_hydrate):
        run = await run_screen(db, policy, None, as_of=ASOF,
                               universe_name="global", hydrate=True)
    assert hydrated == ["NOPOOL"]
    assert "hydrated 1" in (run.error or "")
    res = (await db.execute(
        select(ScreeningResult)
        .where(ScreeningResult.run_id == run.id))).scalar_one()
    assert res.instrument_id == inst.id


async def test_reporting_currency_helper(db):
    from app.services.fundamentals_query import reporting_currency
    inst = Instrument(symbol="CCYT", name="CcyCo")
    db.add(inst)
    await db.flush()
    db.add(_obs(inst.id, "ifrs-full:Revenue", 1e9, date(2025, 12, 31),
                currency="TWD"))
    db.add(_obs(inst.id, "ifrs-full:Revenue", 9e8, date(2024, 12, 31),
                currency="TWD"))
    await db.commit()
    assert await reporting_currency(db, inst.id, ASOF) == "TWD"
    # no currency → None → criteria evaluate permissively
    inst2 = Instrument(symbol="NOCCY", name="NoCcy")
    db.add(inst2)
    await db.flush()
    assert await reporting_currency(db, inst2.id, ASOF) is None


async def test_coverage_missing_helper(db):
    from app.services.universe import coverage_missing
    inst = Instrument(symbol="COVC", name="CovCo")
    db.add(inst)
    await db.flush()
    assert await coverage_missing(db, inst) is True
    db.add(OhlcvBar(instrument_id=inst.id, timeframe="1d", time=ASOF,
                    open=1, high=1, low=1, close=1, volume=1,
                    source="yahoo"))
    await db.flush()
    assert await coverage_missing(db, inst) is True  # bars, no facts
    db.add(_obs(inst.id, "us-gaap:Revenues", 1e6, date(2025, 1, 31)))
    await db.flush()
    assert await coverage_missing(db, inst) is False
