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
    # 11 FY points (2015→2025, a full 10y span) compounding ~10-15%
    # annually — the growth criteria are 10Y CAGR ≥ 10% per the spec,
    # so a pass must come from a real decade of history
    def _grow(base, g, n=11):
        return [(2015 + i, round(base * g ** i, 1)) for i in range(n)]
    _add_obs(
        db, inst,
        {
            "rev": _grow(460, 1.101),        # ~10.1% CAGR → 1205
            "ni": _grow(33, 1.147),          # ~14.7% CAGR → 130
            "ocf": _grow(65, 1.101),         # ~10.1% CAGR → 170
            "ebit": _grow(84, 1.10),         # ~10% CAGR → 218
            "shares": [(2023, 105), (2024, 102), (2025, 100)],
            "capex": [(2023, 30), (2024, 30), (2025, 30)],
            "dps": [(2023, 1.0), (2024, 1.1), (2025, 1.25)],
            "ar": [(2023, 95), (2024, 100), (2025, 105)],
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
    # evidence carries the full CAGR trail, not a one-year delta
    ev = keys["revenue_growth"]["evidence"]
    assert ev["years"] >= 9 and ev["source"] == "xbrl"
    assert ev["cagr"] == pytest.approx(0.10, abs=0.005)
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


async def test_short_history_growth_is_review_not_pass(db):
    """A 2y series is not a 10Y CAGR — the spec's growth bar can't be
    met on a stub of history. Status 'review' flags it for a human;
    it must never read as a pass or a fail."""
    inst = Instrument(symbol="YOUNG", name="YoungCo")
    db.add(inst)
    await db.flush()
    _add_obs(
        db, inst,
        {"rev": [(2024, 100), (2025, 160)],   # +60% YoY — still review
         "ni": [(2024, 10), (2025, 18)],
         "ocf": [(2024, 12), (2025, 20)]},
    )
    await db.commit()
    res = await screen_instrument(db, inst, POLICY_DEFAULTS, None, ASOF)
    for k in ("revenue_growth", "net_income_growth", "ocf_growth"):
        c = _crit(res, k)
        assert c["status"] == "review"
        assert c["review_required"] is True
        assert c["score"] == 0.5
        assert c["evidence"]["years"] < 5


async def test_stockrow_extends_short_history(db, monkeypatch):
    """Local XBRL covering <5y → StockRow's ~10y annual series fills
    the span; the criterion passes on the extended history and the
    evidence attributes the source."""
    from datetime import date as _date
    inst = Instrument(symbol="SREXT", name="SrExt")
    db.add(inst)
    await db.flush()
    _add_obs(db, inst, {"rev": [(2024, 900), (2025, 1100)]})
    await db.commit()

    async def fake_series(self, ticker, slug, limit=12):
        assert slug == "revenue"
        return {_date(2015 + i, 1, 31): 400.0 * 1.11 ** i
                for i in range(11)}
    monkeypatch.setattr(
        "app.providers.stockrow.StockRowAdapter.annual_series",
        fake_series)
    res = await screen_instrument(db, inst, POLICY_DEFAULTS, None, ASOF)
    c = _crit(res, "revenue_growth")
    assert c["status"] == "pass"
    assert c["evidence"]["source"] == "stockrow"
    assert c["evidence"]["years"] >= 9
    assert c["evidence"]["cagr"] == pytest.approx(0.11, abs=0.005)


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


async def test_score_15_is_conditional_18_is_strong(db):
    """Doc gate: <18/20 → WATCHLIST, ≥15/20 → DEEP RESEARCH. The
    15–17 band is REVIEW (conditional, pending human read), never a
    silent pass; ≥90% of applicable criteria passes outright."""
    from app.services.green_zone import aggregate_verdict

    crits15 = ([{"key": f"c{i}", "status": "pass", "score": 1.0}
                for i in range(15)]
               + [{"key": f"f{i}", "status": "fail", "score": 0.0}
                  for i in range(5)])
    r15 = aggregate_verdict(crits15, 15)
    assert r15["verdict"] == "review" and r15["band"] == "conditional"
    assert r15["qualified"] is False

    crits18 = ([{"key": f"c{i}", "status": "pass", "score": 1.0}
                for i in range(18)]
               + [{"key": f"f{i}", "status": "fail", "score": 0.0}
                  for i in range(2)])
    r18 = aggregate_verdict(crits18, 15)
    assert r18["verdict"] == "pass" and r18["band"] == "strong"
    assert r18["qualified"] is True

    # a risk-blocked listing stays blocked regardless of score
    rb = aggregate_verdict(crits18, 15, listing_status="delisted")
    assert rb["verdict"] == "blocked_by_risk"


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


# ── batch price/IV feed + human confirm path ──

async def test_run_screen_feeds_price_and_iv(db):
    """The batch screen must feed latest close + latest ValuationRun
    IV — otherwise P/FCF and IV-discount can never score (the doc's
    margin-of-safety criterion was silently zeroed)."""
    from app.models.market import OhlcvBar
    from app.models.screening import ScreeningResult
    from app.models.universe import Universe, UniverseMembership
    from app.models.valuation import ValuationRun
    from app.services.green_zone import run_screen

    u = Universe(name="approved", tier="approved")
    inst = Instrument(symbol="FEED", name="FeedCo")
    db.add_all([u, inst])
    await db.flush()
    db.add(UniverseMembership(universe_id=u.id, instrument_id=inst.id))
    db.add(OhlcvBar(instrument_id=inst.id, timeframe="1d", time=ASOF,
                    open=1, high=1, low=1, close=50, volume=1,
                    source="yahoo"))
    # minimal fundamentals so FCF/share is computable
    _add_obs(db, inst,
             {"ocf": [(2025, 170)], "capex": [(2025, 30)],
              "shares": [(2025, 100)]})
    db.add(ValuationRun(
        group_id="g1", instrument_id=inst.id, version=1,
        methodology="rule1", mandate_version=None,
        inputs={}, outputs={"rule1": {"sticker_price": 100}}))
    policy = ScreeningPolicy(version=1, is_active=True,
                             params=POLICY_DEFAULTS)
    db.add(policy)
    await db.flush()

    run = await run_screen(db, policy, None, as_of=ASOF)
    res = (await db.execute(
        select(ScreeningResult)
        .where(ScreeningResult.run_id == run.id))).scalar_one()
    crits = {c["key"]: c for c in res.criteria}
    assert crits["p_fcf"]["status"] != "insufficient_data"
    assert crits["iv_discount"]["status"] != "insufficient_data"
    assert crits["iv_discount"]["evidence"]["iv"] == 100.0
    assert crits["iv_discount"]["evidence"]["price"] == 50.0


async def test_confirm_clears_review_and_recomputes(db):
    """Human sign-off on a REVIEW criterion (the doc's 'pending human
    confirmation' path): moat → pass, verdict recomputed, audited."""
    from app.models.identity import User
    from app.models.screening import ScreeningResult, ScreeningRun
    from app.routers.screener import ConfirmIn, confirm

    run = ScreeningRun(universe="approved", policy_version=1,
                       mandate_version=None, status="complete")
    inst = Instrument(symbol="CONF", name="ConfCo")
    db.add_all([run, inst])
    await db.flush()
    res = ScreeningResult(
        run_id=run.id, instrument_id=inst.id,
        score=18.5, applicable=20,
        qualified=False, verdict="review",
        blocked_reasons=[],
        criteria=[{"key": f"c{i}", "name": f"c{i}", "status": "pass",
                   "score": 1.0, "formula": "", "evidence": {}}
                  for i in range(18)]
                 + [{"key": "moat", "name": "Economic moat",
                     "status": "review", "score": 0.5, "formula": "",
                     "evidence": {}, "review_required": True},
                    {"key": "f0", "name": "f0", "status": "fail",
                     "score": 0.0, "formula": "", "evidence": {}}],
        as_of=ASOF)
    db.add(res)
    await db.flush()

    out = await confirm("conf", ConfirmIn(criterion="moat", note="ok"),
                        db, User(id="u-t", email="a@x", is_active=True))
    assert out["verdict"] == "pass" and out["qualified"] is True
    moat = next(c for c in res.criteria if c["key"] == "moat")
    assert moat["status"] == "pass" and moat["score"] == 1.0
    assert moat["evidence"]["confirmed_by"] == "a@x"

    # confirming a non-pending criterion → 409
    import pytest as _pt
    from fastapi import HTTPException
    with _pt.raises(HTTPException) as ei:
        await confirm("conf", ConfirmIn(criterion="moat"), db,
                      User(id="u-t2", email="b@x", is_active=True))
    assert ei.value.status_code == 409


async def test_run_screen_adhoc_symbols(db):
    """symbols=[...] screens the named instruments regardless of
    universe membership — the screen is a lens, not a gated list.
    Unknown symbols are recorded, not silently skipped."""
    from app.models.screening import ScreeningResult
    from app.services.green_zone import run_screen

    inst = Instrument(symbol="ADHOC", name="AdhocCo")
    db.add(inst)
    policy = ScreeningPolicy(version=1, is_active=True,
                             params=POLICY_DEFAULTS)
    db.add(policy)
    await db.flush()

    run = await run_screen(db, policy, None, as_of=ASOF,
                           symbols=["adhoc", "NOPE"])
    assert run.universe.startswith("custom:")
    res = (await db.execute(
        select(ScreeningResult)
        .where(ScreeningResult.run_id == run.id))).scalars().all()
    assert len(res) == 1                     # ADHOC screened, NOPE
    assert "NOPE" in (run.error or "")       #   named in the error
    assert res[0].instrument_id == inst.id


async def test_run_screen_caps_broad_universe(db):
    """A universe larger than max_symbols truncates honestly — the
    run records the cap instead of pretending full coverage."""
    from app.models.screening import ScreeningResult
    from app.models.universe import Universe, UniverseMembership
    from app.services.green_zone import run_screen

    u = Universe(name="eligible", tier="eligible")
    db.add(u)
    insts = [Instrument(symbol=f"T{i}", name=f"T{i}Co",
                        market_cap=float(1000 - i))
             for i in range(4)]
    db.add_all(insts)
    await db.flush()
    for i in insts:
        db.add(UniverseMembership(universe_id=u.id, instrument_id=i.id))
    policy = ScreeningPolicy(version=1, is_active=True,
                             params=POLICY_DEFAULTS)
    db.add(policy)
    await db.flush()

    run = await run_screen(db, policy, None, as_of=ASOF,
                           universe_name="eligible", max_symbols=2)
    res = (await db.execute(
        select(ScreeningResult)
        .where(ScreeningResult.run_id == run.id))).scalars().all()
    assert len(res) == 2
    assert "truncated" in (run.error or "")
    assert run.status == "complete"


async def test_run_screen_survives_hydration_rollback(db, monkeypatch):
    """Regression for the MissingGreenlet run-killer: a rollback
    during lazy hydration expires every ORM object in the session.
    Screening the NEXT member must not trip implicit lazy reload —
    attribute snapshots + per-member savepoints keep the run alive,
    and a failed hydration still screens the member as no-data."""
    from app.models.screening import ScreeningResult
    from app.models.universe import Universe, UniverseMembership
    import app.services.universe as uni
    from app.services.green_zone import run_screen

    u = Universe(name="eligible", tier="eligible")
    db.add(u)
    insts = [Instrument(symbol=f"R{i}", name=f"R{i}Co")
             for i in range(3)]
    db.add_all(insts)
    await db.flush()
    for i in insts:
        db.add(UniverseMembership(universe_id=u.id, instrument_id=i.id))
    policy = ScreeningPolicy(version=1, is_active=True,
                             params=POLICY_DEFAULTS)
    db.add(policy)
    await db.flush()

    async def boom(db_, inst_):
        # worst case: hydrate legs failed so hard a FULL rollback ran —
        # every member object is now expired
        await db_.rollback()
        raise RuntimeError("provider exploded")
    monkeypatch.setattr(uni, "hydrate_instrument", boom)

    run = await run_screen(db, policy, None, as_of=ASOF,
                           universe_name="eligible", hydrate=True)
    assert run.status == "complete"
    assert run.instruments == 3          # no member lost to the crash
    res = (await db.execute(
        select(ScreeningResult)
        .where(ScreeningResult.run_id == run.id))).scalars().all()
    assert len(res) == 3
    assert all(r.verdict == "insufficient_data" for r in res)
