"""Phase-1 qualification gate — five-numbers horizons, four-M proxies,
valuation zones, verdict ladder."""
import pytest
from datetime import date, datetime, timedelta

from app.models.fundamentals import FundamentalObservation as FO
from app.models.instruments import Instrument
from app.models.market import OhlcvBar
from app.models.valuation import ValuationRun
from app.services import qualification as qual


def _fy(inst, concept, end_year, value, start=None):
    """Annual duration fact ending Jan 31 of end_year (NVDA-style FY)."""
    end = date(end_year, 1, 31)
    return FO(instrument_id=inst.id, concept=concept, value=value,
              unit="USD", period_start=start or date(end_year - 1, 2, 1),
              period_end=end, fiscal_period="FY",
              observed_at=datetime(2026, 3, 1),
              published_at=datetime(2026, 3, 1),
              source="edgar", source_ref=f"accn-{concept}-{end_year}")


def _instant(inst, concept, end_year, value):
    end = date(end_year, 1, 31)
    return FO(instrument_id=inst.id, concept=concept, value=value,
              unit="USD", period_start=None, period_end=end,
              fiscal_period="FY", observed_at=datetime(2026, 3, 1),
              published_at=datetime(2026, 3, 1),
              source="edgar", source_ref=f"accn-{concept}-{end_year}")


async def _seed_growth_company(db, inst):
    """12y of compounding fundamentals — all five numbers pass."""
    for i, yr in enumerate(range(2015, 2027)):
        f = 1.2 ** i
        db.add_all([
            _fy(inst, "us-gaap:Revenues", yr, 10_000 * f),
            _fy(inst, "us-gaap:NetIncomeLoss", yr, 1_000 * f),
            _fy(inst, "us-gaap:NetCashProvidedByUsedInOperatingActivities",
                yr, 1_200 * f),
            _fy(inst, "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
                yr, 200 * f),
            _fy(inst, "us-gaap:OperatingIncomeLoss", yr, 1_500 * f),
            _fy(inst, "us-gaap:WeightedAverageNumberOfSharesOutstandingBasic",
                yr, 100),                       # flat — no dilution
            _fy(inst, "us-gaap:IncomeTaxExpenseBenefit", yr, 300 * f),
            _fy(inst, "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                yr, 1_800 * f),
            _instant(inst, "us-gaap:StockholdersEquity", yr, 5_000 * f),
            _instant(inst, "us-gaap:LongTermDebt", yr, 500),
            _instant(inst, "us-gaap:CashAndCashEquivalentsAtCarryingValue",
                     yr, 2_000 * f),
            _instant(inst, "us-gaap:AssetsCurrent", yr, 3_000 * f),
            _instant(inst, "us-gaap:LiabilitiesCurrent", yr, 1_500 * f),
        ])
    await db.flush()


@pytest.fixture
async def inst(db):
    i = Instrument(symbol="TEST", name="Test Co", asset_class="equity")
    db.add(i)
    await db.flush()
    return i


async def test_five_numbers_multi_horizon(db, inst):
    await _seed_growth_company(db, inst)
    g = await qual.qualification_gate(
        db, inst, price=200,
        as_of=datetime(2026, 3, 5))
    five = g["five_numbers"]
    # ~20%/yr compounding → all horizons ≈ 0.20
    for metric in ("revenue", "eps", "fcf_per_share", "bvps"):
        row = five[metric]
        assert row["10y"] == pytest.approx(0.20, abs=0.01), metric
        assert row["3y"] == pytest.approx(0.20, abs=0.01), metric
        assert row["pass"] is True, metric
        assert row["consistency"] == 1.0
    # ROIC level check: NOPAT/(debt+equity−cash)
    assert five["roic"]["latest"] is not None
    assert five["all_pass"] is True


async def test_four_ms_and_verdict_eligible(db, inst):
    await _seed_growth_company(db, inst)
    # MOS at 200 → price 100 sits inside the buy zone → ELIGIBLE
    db.add(ValuationRun(
        group_id="g", instrument_id=inst.id, version=1,
        methodology="rule1-dcf/v2.0", inputs={}, outputs={
            "rule1": {"sticker_price": 400.0, "buy_price": 200.0},
            "dcf": {"per_share": 380.0}}))
    db.add(OhlcvBar(instrument_id=inst.id, timeframe="1d",
                    time=datetime(2026, 3, 4, 13, 30),
                    open=99, high=101, low=98, close=100, volume=1e6,
                    source="yahoo"))
    await db.flush()
    g = await qual.gate_with_price(db, inst)
    assert g["verdict"] == "TRADE_ELIGIBLE"
    assert g["four_ms"]["pass"] is True
    assert g["valuation"]["rule1"]["zone"] == "BUY_ZONE"
    assert g["valuation"]["status"] == "DEEP_VALUE"  # disc 75%


async def test_above_mos_is_watchlist(db, inst):
    await _seed_growth_company(db, inst)
    db.add(ValuationRun(
        group_id="g", instrument_id=inst.id, version=1,
        methodology="rule1-dcf/v2.0", inputs={}, outputs={
            "rule1": {"sticker_price": 150.0, "buy_price": 75.0}}))
    await db.flush()
    g = await qual.qualification_gate(db, inst, price=120,
                                      as_of=datetime(2026, 3, 5))
    assert g["valuation"]["rule1"]["zone"] == "WATCH"
    assert g["verdict"] == "WATCHLIST"


async def test_insufficient_data_rejects_cleanly(db, inst):
    # no fundamentals at all → gate must not fabricate a pass
    g = await qual.qualification_gate(db, inst, price=50,
                                      as_of=datetime(2026, 3, 5))
    assert g["verdict"] in ("REJECTED", "WATCH", "WATCHLIST")
    assert g["four_ms"]["pass"] is False
    assert g["valuation"]["status"] == "INSUFFICIENT_DATA"


async def test_coverage_guard_no_fake_10y(db, inst):
    # only 3 years filed → 10y cells are null, never extrapolated
    for i, yr in enumerate(range(2024, 2027)):
        db.add_all([
            _fy(inst, "us-gaap:Revenues", yr, 1000 * (1.2 ** i)),
            _fy(inst, "us-gaap:NetIncomeLoss", yr, 100 * (1.2 ** i)),
            _fy(inst, "us-gaap:WeightedAverageNumberOfSharesOutstandingBasic",
                yr, 50),
            _instant(inst, "us-gaap:StockholdersEquity", yr, 500 * (1.1 ** i)),
        ])
    await db.flush()
    g = await qual.qualification_gate(db, inst, price=20,
                                      as_of=datetime(2026, 3, 5))
    rev = g["five_numbers"]["revenue"]
    assert rev["10y"] is None and rev["5y"] is None
    assert rev["3y"] is not None      # real coverage → computed
    assert rev["horizon_used"] == "3y"
