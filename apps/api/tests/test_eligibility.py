"""Trade Eligibility Object — unified gate composition.

quality × valuation × technical × margin × portfolio → one verdict
with named blocking reasons. The technical engine is stubbed (its own
suite covers indicator math); these tests verify gate COMPOSITION.
"""
import pytest
from datetime import date, datetime

from app.models.fundamentals import FundamentalObservation as FO
from app.models.instruments import Instrument
from app.models.market import OhlcvBar
from app.models.valuation import ValuationRun
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
                yr, 100),
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


@pytest.fixture
def sleeve_cfg():
    return re_.sleeve_config(re_.Limits(values={
        "sleeve_enabled": True, "sleeve_pct": 0.30,
        "sleeve_target_leverage": 5.0, "sleeve_initial_margin": 0.20,
        "sleeve_maint_margin": 0.16, "sleeve_max_positions": 5,
        "sleeve_max_asset_gross_pct": 0.20,
        "sleeve_starter_fraction": 0.25,
        "sleeve_portfolio_stop_pct": 0.20}))


def _ctx(cfg, gross=0.0, positions=None, open_positions=0):
    st = re_.sleeve_state(100_000, gross, cfg,
                          open_positions=open_positions)
    return {"nav": 100_000, "cash": 100_000, "positions": [],
            "sleeve": {"enabled": True, "config": cfg, "state": st,
                       "positions": positions or []}}


def _stub_technical(monkeypatch, decision="entry_signal", atr=10.0,
                    close=100.0):
    async def _eval(db, inst, as_of):
        return {"decision": decision,
                "mean_reversion": {"decision": "wait"},
                "trend_following": {"decision": decision},
                "data_fresh": True, "last_close": close,
                "indicators": {"atr_14": atr}}
    monkeypatch.setattr(elig.te, "evaluate", _eval)


async def _make_eligible_fundamentals(db, inst, mos=200.0, price=100.0):
    """Growth company + a run whose buy_price clears the entry price."""
    await _seed_growth_company(db, inst)
    db.add(ValuationRun(
        group_id="g", instrument_id=inst.id, version=1,
        methodology="rule1-dcf/v2.0", inputs={}, outputs={
            "rule1": {"sticker_price": mos * 2, "buy_price": mos},
            "dcf": {"per_share": mos * 1.9}}))
    db.add(OhlcvBar(instrument_id=inst.id, timeframe="1d",
                    time=datetime(2026, 3, 4, 13, 30),
                    open=price, high=price + 2, low=price - 2,
                    close=price, volume=1e6, source="yahoo"))
    await db.flush()


async def test_fully_eligible(db, inst, sleeve_cfg, monkeypatch):
    await _make_eligible_fundamentals(db, inst)
    _stub_technical(monkeypatch, "entry_signal", atr=10, close=100)
    e = await elig.trade_eligibility(db, inst, _ctx(sleeve_cfg),
                                     entry=100, atr=10)
    assert e["verdict"] == "TRADE_ELIGIBLE"
    assert e["blocking"] == []
    assert all(g["pass"] for g in e["gates"].values())
    assert e["sizing"]["shares"] > 0


async def test_technical_wait_is_watchlist(db, inst, sleeve_cfg,
                                           monkeypatch):
    await _make_eligible_fundamentals(db, inst)
    _stub_technical(monkeypatch, "wait")
    e = await elig.trade_eligibility(db, inst, _ctx(sleeve_cfg),
                                     entry=100, atr=10)
    # qualified + at/below MOS but no timing signal → WATCHLIST
    assert e["verdict"] == "WATCHLIST"
    assert e["gates"]["technical"]["pass"] is False


async def test_macro_risk_off_blocks_eligibility(db, inst, sleeve_cfg,
                                                 monkeypatch):
    """Doc Step 9 — Macro Regime is a named gate; risk_off blocks
    entries the same way a capacity constraint does (idea gates
    passed → WATCHLIST, not BLOCKED)."""
    await _make_eligible_fundamentals(db, inst)
    _stub_technical(monkeypatch, "entry_signal", atr=10, close=100)
    ctx = _ctx(sleeve_cfg)
    ctx["market_regime"] = "risk_off"
    e = await elig.trade_eligibility(db, inst, ctx,
                                     entry=100, atr=10)
    assert e["verdict"] == "WATCHLIST"
    assert e["gates"]["macro"]["pass"] is False
    assert "macro_risk_off" in e["blocking"]


async def test_macro_missing_skips_not_blocks(db, inst, sleeve_cfg,
                                              monkeypatch):
    """No persisted regime → the gate is skipped (flagged), never a
    phantom blocker."""
    await _make_eligible_fundamentals(db, inst)
    _stub_technical(monkeypatch, "entry_signal", atr=10, close=100)
    e = await elig.trade_eligibility(db, inst, _ctx(sleeve_cfg),
                                     entry=100, atr=10)
    assert e["gates"]["macro"]["skipped"] is True
    assert e["gates"]["macro"]["pass"] is True
    assert e["verdict"] == "TRADE_ELIGIBLE"


async def test_above_mos_is_watchlist_not_eligible(db, inst, sleeve_cfg,
                                                   monkeypatch):
    # price 180 > MOS 100 → qualified but not at the buy price
    await _make_eligible_fundamentals(db, inst, mos=100.0, price=180.0)
    _stub_technical(monkeypatch, "entry_signal")
    e = await elig.trade_eligibility(db, inst, _ctx(sleeve_cfg),
                                     entry=180, atr=10)
    assert e["verdict"] == "WATCHLIST"
    assert e["gates"]["valuation"]["pass"] is False


async def test_no_fundamentals_rejects(db, inst, sleeve_cfg, monkeypatch):
    _stub_technical(monkeypatch, "entry_signal")
    e = await elig.trade_eligibility(db, inst, _ctx(sleeve_cfg),
                                     entry=100, atr=10)
    assert e["verdict"] == "REJECTED"
    assert "four_ms_failed" in e["blocking"]


async def test_sleeve_full_is_blocked(db, inst, sleeve_cfg, monkeypatch):
    await _make_eligible_fundamentals(db, inst)
    _stub_technical(monkeypatch, "entry_signal")
    positions = [{"symbol": s, "market_value": 5_000}
                 for s in ("A", "B", "C", "D", "E")]
    e = await elig.trade_eligibility(
        db, inst, _ctx(sleeve_cfg, gross=25_000, positions=positions,
                       open_positions=5),
        entry=100, atr=10)
    assert e["verdict"] == "BLOCKED"
    assert "sleeve_capacity_exhausted" in e["blocking"]


async def test_no_margin_headroom_is_blocked(db, inst, sleeve_cfg,
                                             monkeypatch):
    await _make_eligible_fundamentals(db, inst)
    _stub_technical(monkeypatch, "entry_signal")
    # gross 120k at 20% maint → cap $120k: zero gross headroom, and
    # free margin = 30k − 24k = 6k can't fund a starter anyway
    ctx = _ctx(sleeve_cfg, gross=120_000)
    st = re_.sleeve_state(100_000, 120_000, sleeve_cfg,
                          maint_margin=0.20)
    ctx["sleeve"]["state"] = st
    e = await elig.trade_eligibility(db, inst, ctx, entry=100, atr=10)
    assert e["verdict"] == "BLOCKED"


async def test_sleeve_disabled_skips_capacity_gates(db, inst,
                                                    monkeypatch):
    await _make_eligible_fundamentals(db, inst)
    _stub_technical(monkeypatch, "entry_signal")
    ctx = {"nav": 100_000, "cash": 100_000, "positions": [],
           "sleeve": {"enabled": False}}
    e = await elig.trade_eligibility(db, inst, ctx,
                                     entry=100, atr=10)
    assert e["verdict"] == "TRADE_ELIGIBLE"
    assert e["gates"]["margin"]["skipped"] is True
    assert e["gates"]["portfolio"]["skipped"] is True


async def test_cooldown_outranks_idea_quality(db, inst, sleeve_cfg,
                                              monkeypatch):
    """Portfolio-stop cooldown → COOLDOWN verdict even when every
    idea-gate passes — release is a human decision."""
    await _make_eligible_fundamentals(db, inst)
    _stub_technical(monkeypatch, "entry_signal")
    ctx = _ctx(sleeve_cfg)
    ctx["sleeve"]["cooldown"] = True
    e = await elig.trade_eligibility(db, inst, ctx,
                                     entry=100, atr=10)
    assert e["verdict"] == "COOLDOWN"
    assert "sleeve_cooldown" in e["blocking"]


async def test_object_shape_is_machine_readable(db, inst, sleeve_cfg,
                                                monkeypatch):
    await _make_eligible_fundamentals(db, inst)
    _stub_technical(monkeypatch, "wait")
    e = await elig.trade_eligibility(db, inst, _ctx(sleeve_cfg))
    assert set(e["gates"]) == {"quality", "valuation", "technical",
                               "macro", "margin", "portfolio"}
    assert isinstance(e["blocking"], list)
    assert e["qualification_verdict"] in (
        "TRADE_ELIGIBLE", "WATCHLIST", "WATCH", "REJECTED")
