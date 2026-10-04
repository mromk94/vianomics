"""Step-25 Decision Object — standardized verdict composition,
verdict mapping, CIO-advisory semantics, approval invariant."""
import pytest
from datetime import date, datetime

from app.models.fundamentals import FundamentalObservation as FO
from app.models.instruments import Instrument
from app.models.market import OhlcvBar
from app.models.valuation import ValuationRun
from app.services import decision_object as dobj
from app.services import eligibility as elig
from app.services import risk_engine as re_


def _fy(inst, concept, end_year, value, start=None):
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
    # real-world magnitudes — the Step-1.2 screen checks market cap
    # and P/E off raw filed units (200M shares, billion-scale $)
    for i, yr in enumerate(range(2015, 2027)):
        f = 1.2 ** i
        db.add_all([
            _fy(inst, "us-gaap:Revenues", yr, 10_000_000_000 * f),
            _fy(inst, "us-gaap:NetIncomeLoss", yr, 1_000_000_000 * f),
            _fy(inst, "us-gaap:NetCashProvidedByUsedInOperatingActivities",
                yr, 1_200_000_000 * f),
            _fy(inst, "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
                yr, 200_000_000 * f),
            _fy(inst, "us-gaap:OperatingIncomeLoss", yr, 1_500_000_000 * f),
            _fy(inst, "us-gaap:WeightedAverageNumberOfSharesOutstandingBasic",
                yr, 200_000_000),
            _fy(inst, "us-gaap:IncomeTaxExpenseBenefit", yr, 300_000_000 * f),
            _fy(inst, "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                yr, 1_800_000_000 * f),
            _instant(inst, "us-gaap:StockholdersEquity", yr, 5_000_000_000 * f),
            _instant(inst, "us-gaap:LongTermDebt", yr, 500_000_000),
            _instant(inst, "us-gaap:CashAndCashEquivalentsAtCarryingValue",
                     yr, 2_000_000_000 * f),
            _instant(inst, "us-gaap:AssetsCurrent", yr, 3_000_000_000 * f),
            _instant(inst, "us-gaap:LiabilitiesCurrent", yr, 1_500_000_000 * f),
        ])
    await db.flush()


@pytest.fixture
async def inst(db):
    i = Instrument(symbol="TEST", name="Test Co", asset_class="equity")
    db.add(i)
    await db.flush()
    return i


def _ctx():
    cfg = re_.sleeve_config(re_.Limits(values={"sleeve_enabled": True}))
    st = re_.sleeve_state(100_000, 0, cfg)
    return {"nav": 100_000, "cash": 100_000, "positions": [],
            "sleeve": {"enabled": True, "config": cfg, "state": st,
                       "positions": []}}


def _stub_technical(monkeypatch, decision="entry_signal"):
    async def _eval(db, inst, as_of):
        return {"decision": decision,
                "mean_reversion": {"decision": "wait"},
                "trend_following": {"decision": decision},
                "data_fresh": True, "last_close": 100.0,
                "indicators": {"atr_14": 10.0}}
    monkeypatch.setattr(elig.te, "evaluate", _eval)


async def _eligible(db, inst):
    await _seed_growth_company(db, inst)
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


async def test_eligible_maps_to_enter(db, inst, monkeypatch):
    await _eligible(db, inst)
    _stub_technical(monkeypatch)
    o = await dobj.decision_object(db, inst, _ctx())
    assert o["decision"] == "ENTER"
    assert o["eligibility_verdict"] == "TRADE_ELIGIBLE"
    assert o["blocking"] == []
    assert o["confidence"] == 1.0
    assert o["approval_required"] is True


async def test_rejected_maps_to_reject(db, inst, monkeypatch):
    _stub_technical(monkeypatch)
    o = await dobj.decision_object(db, inst, _ctx())
    assert o["decision"] == "REJECT"
    assert o["eligibility_verdict"] == "REJECTED"
    assert "four_ms_failed" in o["blocking"]
    assert o["confidence"] < 1.0
    assert o["approval_required"] is True   # invariant, always


async def test_cooldown_maps_to_block(db, inst, monkeypatch):
    await _eligible(db, inst)
    _stub_technical(monkeypatch)
    ctx = _ctx()
    ctx["sleeve"]["cooldown"] = True
    o = await dobj.decision_object(db, inst, ctx)
    assert o["decision"] == "BLOCK"
    assert o["eligibility_verdict"] == "COOLDOWN"
    assert "sleeve_cooldown" in o["risk_flags"]


async def test_cio_advisory_is_annotation_not_gate(db, inst,
                                                   monkeypatch):
    """A prior committee APPROVE can never override a failing gate —
    the object exposes it as advisory beside the real verdict."""
    from app.models.governance import DecisionRecord
    _stub_technical(monkeypatch)
    db.add(DecisionRecord(
        instrument_id=inst.id, stage="researched",
        verdict="approve_pending_human", gate_results={},
        agent_scores={}, numbers={}, cio_confidence=0.8))
    await db.flush()
    o = await dobj.decision_object(db, inst, _ctx())
    assert o["decision"] == "REJECT"            # gate still fails
    assert o["cio_advisory"]["verdict"] == "approve_pending_human"
    assert "advisory" in o["cio_advisory"]["note"]


async def test_stage_results_complete(db, inst, monkeypatch):
    await _eligible(db, inst)
    _stub_technical(monkeypatch, "wait")
    o = await dobj.decision_object(db, inst, _ctx())
    assert set(o["stages"]) == {"quality", "valuation", "technical",
                                "macro", "margin", "portfolio",
                                "sleeve"}
    assert o["stages"]["valuation"]["mos_price"] == 200.0
    assert o["stages"]["technical"]["decision"] == "wait"
    assert o["decision"] == "WATCH"
    assert o["engine_refs"]["risk"] == re_.ENGINE_VERSION
