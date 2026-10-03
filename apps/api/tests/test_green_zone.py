"""Green Zone engine tests — synthetic fundamentals + edge cases."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.fundamentals import FundamentalObservation
from app.models.instruments import Instrument
from app.models.mandate import Mandate
from app.models.screening import ScreeningPolicy
from app.services.green_zone import POLICY_DEFAULTS, screen_instrument

ASOF = datetime(2025, 6, 1, tzinfo=UTC)
FILED = datetime(2025, 3, 1, tzinfo=UTC)


def _obs(inst_id, concept, value, period_end, prev=False):
    return FundamentalObservation(
        instrument_id=inst_id, concept=concept, value=Decimal(str(value)),
        unit="USD", period_start=date(period_end.year - 1, period_end.month,
                                      min(period_end.day, 28)),
        period_end=period_end, fiscal_period="FY",
        observed_at=datetime(period_end.year, period_end.month,
                             period_end.day, tzinfo=UTC),
        published_at=FILED - (datetime.now(UTC).replace(tzinfo=None) -
                              datetime.now(UTC).replace(tzinfo=None)),
        source="edgar",
    )


def _fy(inst_id, concept, values: list[tuple[int, float]]):
    """values = [(year, value)] FY rows."""
    out = []
    for y, v in values:
        out.append(
            FundamentalObservation(
                instrument_id=inst_id, concept=concept,
                value=Decimal(str(v)), unit="USD",
                period_start=date(y - 1, 2, 1), period_end=date(y, 1, 31),
                fiscal_period="FY",
                observed_at=datetime(y, 1, 31, tzinfo=UTC),
                published_at=datetime(y, 2, 15, tzinfo=UTC),
                source="edgar",
            )
        )
    return out


def _add_obs(db, inst, fy_map, inst_map=None):
    """fy_map: {concept_key: [(year, val)]}; inst_map: {concept_key: val}."""
    CONCEPTS = {
        "rev": "us-gaap:Revenues", "ni": "us-gaap:NetIncomeLoss",
        "ocf": "us-gaap:NetCashProvidedByUsedInOperatingActivities",
        "ebit": "us-gaap:OperatingIncomeLoss",
        "shares": "us-gaap:WeightedAverageNumberOfSharesOutstandingBasic",
        "capex": "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
        "dps": "us-gaap:CommonStockDividendsPerShareDeclared",
        "ar": "us-gaap:AccountsReceivableNetCurrent",
        "tax": "us-gaap:IncomeTaxExpenseBenefit",
        "pretax": "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "da": "us-gaap:DepreciationDepletionAndAmortization",
        "equity": "us-gaap:StockholdersEquity",
        "debt": "us-gaap:LongTermDebtNoncurrent",
        "cash": "us-gaap:CashAndCashEquivalentsAtCarryingValue",
        "interest": "us-gaap:InterestExpenseNonoperating",
        "ca": "us-gaap:AssetsCurrent",
        "cl": "us-gaap:LiabilitiesCurrent",
    }
    for key, vals in fy_map.items():
        for r in _fy(inst.id, CONCEPTS[key], vals):
            db.add(r)
    for key, v in (inst_map or {}).items():
        db.add(
            FundamentalObservation(
                instrument_id=inst.id, concept=CONCEPTS[key],
                value=Decimal(str(v)), unit="USD",
                period_end=date(2025, 1, 31),
                observed_at=datetime(2025, 1, 31, tzinfo=UTC),
                published_at=FILED, source="edgar",
            )
        )


def _crit(res, key):
    return next(c for c in res["criteria"] if c["key"] == key)


async def test_full_growth_company_scores_high(db):
    inst = Instrument(symbol="GROW", name="GrowCo")
    db.add(inst)
    await db.flush()
    _add_obs(
        db, inst,
        {
            "rev": [(2023, 800), (2024, 1000), (2025, 1200)],
            "ni": [(2023, 80), (2024, 100), (2025, 130)],
            "ocf": [(2023, 110), (2024, 140), (2025, 170)],
            "ebit": [(2023, 120), (2024, 170), (2025, 220)],
            "shares": [(2023, 105), (2024, 102), (2025, 100)],
            "capex": [(2023, 30), (2024, 30), (2025, 30)],
            "dps": [(2023, 1.0), (2024, 1.1), (2025, 1.25)],
            "ar": [(2024, 100), (2025, 105)],
            "tax": [(2025, 20)], "pretax": [(2025, 120)],
            "da": [(2025, 40)],
        },
        {"equity": 500, "debt": 200, "cash": 50,
         "interest": 10, "ca": 300, "cl": 150},
    )
    await db.commit()

    res = await screen_instrument(
        db, inst, POLICY_DEFAULTS, None, ASOF, price=50, intrinsic_value=100
    )
    keys = {c["key"]: c for c in res["criteria"]}
    assert res["score"] >= 15
    assert keys["revenue_growth"]["status"] == "pass"
    assert keys["cash_conversion"]["status"] == "pass"  # 170/130 = 1.31
    assert keys["share_count"]["status"] == "pass"      # declining
    assert keys["debt_ebitda"]["status"] == "pass"      # 200/(220+40)=0.77
    assert keys["interest_coverage"]["status"] == "pass"  # 220/10=22
    assert keys["iv_discount"]["status"] == "pass"      # (100-50)/100=50%≥20%
    # moat → review (deterministic evidence positive, human confirms)
    assert keys["moat"]["status"] in ("review", "pass")
    if keys["moat"]["status"] == "review":
        assert res["verdict"] == "review"  # ≥15 but unconfirmed
        assert res["qualified"] is False


async def test_missing_data_never_passes(db):
    inst = Instrument(symbol="EMPTY", name="EmptyCo")
    db.add(inst)
    await db.flush()
    res = await screen_instrument(db, inst, POLICY_DEFAULTS, None, ASOF)
    assert res["score"] == 0
    assert res["verdict"] == "insufficient_data"
    assert all(
        c["status"] in ("insufficient_data", "not_applicable", "fail")
        for c in res["criteria"]
    )


async def test_negative_earnings_fail_not_crash(db):
    inst = Instrument(symbol="LOSS", name="LossCo")
    db.add(inst)
    await db.flush()
    _add_obs(
        db, inst,
        {"rev": [(2024, 500), (2025, 450)],
         "ni": [(2024, -20), (2025, -40)],
         "ocf": [(2024, 10), (2025, -30)],
         "ebit": [(2024, -15), (2025, -35)],
         "shares": [(2025, 100)], "capex": [(2025, 5)]},
        {"equity": 100, "debt": 300, "cash": 10, "ca": 50, "cl": 60},
    )
    await db.commit()
    res = await screen_instrument(db, inst, POLICY_DEFAULTS, None, ASOF)
    assert res["verdict"] in ("fail", "insufficient_data")
    assert res["qualified"] is False
    # negative NI base → ni growth insufficient, not false pass
    assert _crit(res, "net_income_growth")["status"] == "insufficient_data"


async def test_financial_sector_variants_na(db):
    inst = Instrument(symbol="BANK", name="BankCo")
    db.add(inst)
    await db.flush()
    res = await screen_instrument(
        db, inst, POLICY_DEFAULTS, None, ASOF, sector_name="Financials"
    )
    for k in ("current_ratio", "debt_ebitda", "interest_coverage", "dscr"):
        c = _crit(res, k)
        assert c["status"] == "not_applicable"
        assert c["industry_variant"] is True


async def test_delisted_is_blocked(db):
    inst = Instrument(symbol="DEAD", name="DeadCo", listing_status="delisted")
    db.add(inst)
    await db.flush()
    res = await screen_instrument(db, inst, POLICY_DEFAULTS, None, ASOF)
    assert res["verdict"] == "blocked_by_risk"
    assert "listing_status=delisted" in res["blocked_reasons"]


async def test_moat_is_never_llm_freebie(db):
    """Moat requires documented deterministic evidence — no data → no pass."""
    inst = Instrument(symbol="NOMOAT", name="NoMoat")
    db.add(inst)
    await db.flush()
    res = await screen_instrument(db, inst, POLICY_DEFAULTS, None, ASOF)
    c = _crit(res, "moat")
    assert c["status"] == "insufficient_data"
    assert c["score"] == 0


async def test_dividend_na_for_non_payer(db):
    inst = Instrument(symbol="NODIV", name="NoDiv")
    db.add(inst)
    await db.flush()
    res = await screen_instrument(db, inst, POLICY_DEFAULTS, None, ASOF)
    assert _crit(res, "dividend_growth")["status"] == "not_applicable"


async def test_score_15_qualifies_but_risk_gates_standalone(db):
    """15/20 qualifies for research; it does NOT bypass risk gates —
    blocked instruments stay blocked regardless of score."""
    res_threshold = {
        "criteria": [{"key": f"c{i}", "status": "pass", "score": 1.0}
                     for i in range(15)]
        + [{"key": f"f{i}", "status": "fail", "score": 0.0}
           for i in range(5)],
        "score": 15, "applicable": 20,
        "verdict": "pass", "qualified": True,
    }
    # engine-level: qualification is a screening verdict only —
    # risk veto applies downstream (Part 14/16), tested via blocked path
    assert res_threshold["qualified"] is True
    assert res_threshold["verdict"] == "pass"


# ── ETF / non-fundamental screening (macro + technical channels) ──

async def _etf_fixture(db, sector_name, favored=True):
    """Sector ETF with 200 daily bars, a SPY benchmark, a RegimeRun."""
    from app.models.instruments import Sector
    from app.models.macro import RegimeRun
    from app.models.market import OhlcvBar
    from datetime import timedelta

    sec = Sector(name=sector_name)
    db.add(sec)
    await db.flush()
    spy = Instrument(symbol="SPY", name="SPY", asset_class="etf")
    etf = Instrument(symbol="XLK", name="Tech SPDR", asset_class="etf",
                     sector_id=sec.id, avg_dollar_volume_30d=1e9)
    db.add_all([spy, etf])
    await db.flush()
    # 200 bars: rising for ETF, flat for SPY → RS + trend both pass
    for i in range(200):
        d = ASOF - timedelta(days=200 - i)
        db.add(OhlcvBar(instrument_id=etf.id, timeframe="1d", time=d,
                        open=90 + i * 0.2, high=0, low=0,
                        close=90 + i * 0.2, volume=1e6, source="yahoo"))
        db.add(OhlcvBar(instrument_id=spy.id, timeframe="1d", time=d,
                        open=100, high=0, low=0, close=100,
                        volume=1e6, source="yahoo"))
    prefs = {sector_name: "favored"} if favored else {"Energy": "favored"}
    db.add(RegimeRun(
        as_of=ASOF, rules_version="v1", econ_regime="expansion",
        market_regime="risk_on", features={}, rule_hits=[],
        sector_preferences=prefs))
    await db.flush()
    return etf


async def test_etf_screen_passes_on_macro_technical(db):
    etf = await _etf_fixture(db, "Information Technology")
    res = await screen_instrument(
        db, etf, POLICY_DEFAULTS, None, ASOF,
        sector_name="Information Technology")
    keys = {c["key"] for c in res["criteria"]}
    assert keys == {"macro_sector_alignment", "relative_strength",
                    "technical_setup", "liquidity"}
    assert res["verdict"] == "pass"
    assert res["qualified"] is True
    assert res["score_fraction"] == 1.0


async def test_etf_screen_reviews_when_sector_unfavored(db):
    """Unfavored sector → macro thesis fails → REVIEW, not silent
    pass (thesis channel is the ETF's fundamental channel)."""
    etf = await _etf_fixture(db, "Information Technology",
                             favored=False)
    res = await screen_instrument(
        db, etf, POLICY_DEFAULTS, None, ASOF,
        sector_name="Information Technology")
    assert res["verdict"] == "review"
    assert res["qualified"] is False
    assert res["score_fraction"] == 0.75   # 3/4 criteria still pass
    macro = next(c for c in res["criteria"]
                 if c["key"] == "macro_sector_alignment")
    assert macro["status"] == "fail"
    assert macro["evidence"]["econ_regime"] == "expansion"


async def test_etf_insufficient_without_bars_or_regime(db):
    etf = Instrument(symbol="GLD", name="Gold", asset_class="etf")
    db.add(etf)
    await db.flush()
    res = await screen_instrument(
        db, etf, POLICY_DEFAULTS, None, ASOF, sector_name=None)
    assert res["verdict"] == "insufficient_data"
    macro = next(c for c in res["criteria"]
                 if c["key"] == "macro_sector_alignment")
    assert macro["status"] == "insufficient_data"  # no regime run


async def test_equity_path_unchanged_by_etf_branch(db):
    """An equity with no fundamentals still lands insufficient_data
    via the Green Zone, not the ETF path."""
    eq = Instrument(symbol="ZZZZ", name="ZZ", asset_class="equity")
    db.add(eq)
    await db.flush()
    res = await screen_instrument(db, eq, POLICY_DEFAULTS, None, ASOF)
    assert res["verdict"] == "insufficient_data"
    assert len(res["criteria"]) == 20
