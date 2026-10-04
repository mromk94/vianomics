"""statement_map — fiscal-calendar classification, YTD derivation,
alias merging, TTM ratios. Mirrors SEC companyfacts semantics."""

import pytest
from datetime import date, datetime

from app.models.fundamentals import FundamentalObservation as FO
from app.models.instruments import Instrument
from app.services.financials import statement_map


def _obs(inst, concept, start, end, fp, value, unit="USD", pub=None):
    return FO(
        instrument_id=inst.id, concept=concept, value=value, unit=unit,
        period_start=start, period_end=end, fiscal_period=fp,
        observed_at=datetime(2025, 8, 1),
        published_at=pub or datetime(2025, 8, 1),
        source="edgar", source_ref="accn-1",
    )


@pytest.fixture
async def inst(db):
    i = Instrument(symbol="TEST", name="Test Co", asset_class="equity")
    db.add(i)
    await db.flush()
    return i


async def test_fiscal_calendar_labels_instants(db, inst):
    # FY end = Jan 26 (12-mo duration fact). An instant at that end
    # tagged "Q2" by a later filing must still classify as FY.
    db.add_all([
        _obs(inst, "us-gaap:Revenues", date(2024, 1, 29),
             date(2025, 1, 26), "FY", 100),
        _obs(inst, "us-gaap:Assets", None, date(2025, 1, 26),
             "Q2", 500),  # comparative instant inside a Q filing
        _obs(inst, "us-gaap:Assets", None, date(2025, 4, 27),
             "Q1", 480),
    ])
    await db.flush()
    fin = await statement_map(db, inst)
    assets = next(c for c in fin["statements"]["balance"]
                  if c["concept"].endswith("Assets"))
    by_end = {p["end"]: p["fp"] for p in assets["points"]}
    assert by_end["2025-01-26"] == "FY"
    assert by_end["2025-04-27"] == "Q1"


async def test_ytd_derives_standalone_quarter(db, inst):
    # Q2 reported only as 6-mo YTD (100) with Q1 3-mo (40) →
    # standalone Q2 = 60, flagged derived.
    db.add_all([
        _obs(inst, "us-gaap:Revenues", date(2024, 1, 29),
             date(2025, 1, 26), "FY", 300),
        _obs(inst, "us-gaap:Revenues", date(2025, 1, 27),
             date(2025, 4, 27), "Q1", 40),
        _obs(inst, "us-gaap:Revenues", date(2025, 1, 27),
             date(2025, 7, 27), "Q2", 100),
    ])
    await db.flush()
    fin = await statement_map(db, inst)
    rev = next(c for c in fin["statements"]["income"]
               if c["concept"].endswith("Revenues"))
    by_end = {p["end"]: p for p in rev["points"]}
    assert by_end["2025-07-27"]["value"] == pytest.approx(60)
    assert by_end["2025-07-27"].get("derived")
    assert by_end["2025-04-27"]["value"] == pytest.approx(40)
    assert by_end["2025-01-26"]["value"] == pytest.approx(300)


async def test_quarterly_prefers_3mo_over_ytd(db, inst):
    # both a 3-mo and 6-mo fact end at the same date → keep the 3-mo
    db.add_all([
        _obs(inst, "us-gaap:Revenues", date(2025, 4, 28),
             date(2025, 7, 27), "Q2", 55),
        _obs(inst, "us-gaap:Revenues", date(2025, 1, 27),
             date(2025, 7, 27), "Q2", 100,
             pub=datetime(2025, 9, 1)),  # newer filing, still loses
    ])
    await db.flush()
    fin = await statement_map(db, inst)
    rev = next(c for c in fin["statements"]["income"]
               if c["concept"].endswith("Revenues"))
    pts = [p for p in rev["points"] if p["end"] == "2025-07-27"]
    assert len(pts) == 1 and pts[0]["value"] == pytest.approx(55)


async def test_alias_merge_fills_missing_periods(db, inst):
    # old-taxonomy Revenue covers 2024 FY; new Revenues covers 2025 —
    # one merged row, primary wins overlapping ends.
    db.add_all([
        _obs(inst, "us-gaap:Revenues", date(2024, 1, 29),
             date(2025, 1, 26), "FY", 300),
        _obs(inst, "us-gaap:Revenue", date(2023, 1, 30),
             date(2024, 1, 28), "FY", 200),
    ])
    await db.flush()
    fin = await statement_map(db, inst)
    rows = [c for c in fin["statements"]["income"]
            if c["concept"].endswith("Revenues")]
    assert len(rows) == 1
    ends = {p["end"]: p["value"] for p in rows[0]["points"]}
    assert ends["2025-01-26"] == pytest.approx(300)
    assert ends["2024-01-28"] == pytest.approx(200)


async def test_ttm_ratios_use_derived_quarters(db, inst):
    # four standalone quarters (one YTD-derived) → TTM EPS/NI,
    # P/E = close / TTM EPS.
    db.add_all([
        _obs(inst, "us-gaap:EarningsPerShareDiluted",
             date(2024, 10, 28), date(2025, 1, 26), "FY", 1.0,
             unit="USD/shares"),
        _obs(inst, "us-gaap:EarningsPerShareDiluted",
             date(2025, 1, 27), date(2025, 4, 27), "Q1", 1.0,
             unit="USD/shares"),
        _obs(inst, "us-gaap:EarningsPerShareDiluted",
             date(2025, 1, 27), date(2025, 7, 27), "Q2", 3.0,
             unit="USD/shares"),  # YTD 3 → standalone 2
        _obs(inst, "us-gaap:EarningsPerShareDiluted",
             date(2025, 7, 28), date(2025, 10, 26), "Q3", 1.5,
             unit="USD/shares"),
    ])
    await db.flush()
    fin = await statement_map(db, inst, close=100.0)
    # TTM EPS = Q4(1.0) + Q1(1.0) + Q2(2.0) + Q3(1.5) = 5.5 → P/E ~18.18
    assert fin["ratios"]["eps_diluted"] == pytest.approx(5.5)
    assert fin["ratios"]["pe"] == pytest.approx(100 / 5.5)


async def test_empty_instrument(db, inst):
    fin = await statement_map(db, inst)
    assert fin["facts"] == 0
    assert fin["periods"] == []
    assert fin["ratios"]["pe"] is None


async def test_filing_tag_fy_on_3mo_fact_normalized(db, inst):
    # a 3-mo duration fact inside a 10-K is tagged "FY" by SEC —
    # display fp must follow duration → it's Q4, not annual.
    db.add(_obs(inst, "us-gaap:Revenues", date(2024, 10, 28),
                date(2025, 1, 26), "FY", 50))
    # a real 12-mo fact at the same end makes it an FY end on calendar
    db.add(_obs(inst, "us-gaap:CostOfRevenue", date(2024, 1, 29),
                date(2025, 1, 26), "FY", 200))
    await db.flush()
    fin = await statement_map(db, inst)
    rev = next(c for c in fin["statements"]["income"]
               if c["concept"].endswith("Revenues"))
    assert rev["points"][0]["fp"] == "Q4"
    cost = next(c for c in fin["statements"]["income"]
                if c["concept"].endswith("CostOfRevenue"))
    assert cost["points"][0]["fp"] == "FY"
