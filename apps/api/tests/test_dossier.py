"""Dossier + sector valuation tests — deterministic fixtures."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.dossier import Dossier, DossierSection, EvidenceItem
from app.models.fundamentals import FundamentalObservation
from app.models.instruments import Instrument, Sector
from app.services import research_orchestrator as ro
from app.services import valuation as val

ASOF = datetime(2025, 6, 1, tzinfo=UTC)


def _fy(inst_id, concept, vals):
    out = []
    for y, v in vals:
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


async def _seeded(db, symbol="TEST", sector=None):
    inst = Instrument(symbol=symbol, name=f"{symbol}Co", sector_id=sector)
    db.add(inst)
    await db.flush()
    base = {
        "us-gaap:Revenues": [(2023, 800), (2024, 1000), (2025, 1200)],
        "us-gaap:NetIncomeLoss": [(2023, 80), (2024, 100), (2025, 130)],
        "us-gaap:NetCashProvidedByUsedInOperatingActivities":
            [(2023, 110), (2024, 140), (2025, 170)],
        "us-gaap:OperatingIncomeLoss": [(2023, 120), (2024, 170), (2025, 220)],
        "us-gaap:WeightedAverageNumberOfSharesOutstandingBasic":
            [(2023, 105), (2024, 102), (2025, 100)],
        "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment":
            [(2025, 30)],
        "us-gaap:AccountsReceivableNetCurrent": [(2024, 100), (2025, 105)],
        "us-gaap:IncomeTaxExpenseBenefit": [(2025, 20)],
        "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest":
            [(2025, 120)],
        "us-gaap:DepreciationDepletionAndAmortization": [(2025, 40)],
        "us-gaap:StockholdersEquity": [(2025, 500)],
    }
    for c, vals in base.items():
        for r in _fy(inst.id, c, vals):
            db.add(r)
    await db.commit()
    return inst


async def test_all_18_sections_present(db):
    inst = await _seeded(db)
    d = await ro.build_dossier(db, inst, None, None, 1, ASOF)
    await db.commit()
    sections = (
        await db.execute(
            select(DossierSection).where(DossierSection.dossier_id == d.id)
        )
    ).scalars().all()
    assert len(sections) == 18
    assert [s.section_no for s in sections] == list(range(1, 19))
    assert [s.title for s in sections] == ro.SECTION_TITLES


async def test_verified_claims_have_evidence(db):
    inst = await _seeded(db)
    d = await ro.build_dossier(db, inst, None, None, 1, ASOF)
    await db.commit()
    secs = (
        await db.execute(
            select(DossierSection).where(DossierSection.dossier_id == d.id)
        )
    ).scalars().all()
    verified = [
        (s, c) for s in secs for c in s.content
        if c["claim_type"] == "verified_fact"
    ]
    assert verified  # there are real claims
    ev_ids = {
        e.id for e in (await db.execute(
            select(EvidenceItem).where(EvidenceItem.instrument_id == inst.id)
        )).scalars()
    }
    for s, c in verified:
        assert c["evidence_ids"], f"§{s.section_no} verified claim w/o evidence"
        assert all(eid in ev_ids for eid in c["evidence_ids"])


async def test_missing_evidence_disclosed(db):
    inst = await _seeded(db)
    d = await ro.build_dossier(db, inst, None, None, 1, ASOF)
    items = [m["item"] for m in d.missing_evidence]
    # no market data provider → price + technicals are honest gaps
    assert any("market price" in i for i in items)
    assert any("peer" in i for i in items)


async def test_no_data_dossier_is_honest(db):
    inst = Instrument(symbol="ZERO", name="Z")
    db.add(inst)
    await db.flush()
    d = await ro.build_dossier(db, inst, None, None, 1, ASOF)
    await db.commit()
    secs = (
        await db.execute(
            select(DossierSection).where(DossierSection.dossier_id == d.id)
        )
    ).scalars().all()
    # every section exists but mostly insufficient — never fabricated
    assert len(secs) == 18
    insuf = sum(
        1 for s in secs
        if any(c["claim_type"] == "insufficient_data" for c in s.content)
    )
    assert insuf >= 10
    # zero fabricated verified facts
    assert all(
        c["claim_type"] != "verified_fact"
        for s in secs for c in s.content
    )


async def test_versioning(db):
    inst = await _seeded(db)
    d1 = await ro.build_dossier(db, inst, None, None, 1, ASOF)
    await db.flush()
    d2 = await ro.build_dossier(db, inst, None, None, 1, ASOF)
    await db.commit()
    assert d2.group_id == d1.group_id and d2.version == 2


# ── sector valuation gates ──

def test_archetype_mapping():
    assert val.archetype_for("Financials") == "financial"
    assert val.archetype_for("Real Estate") == "real_estate"
    assert val.archetype_for("Energy") == "commodity"
    assert val.archetype_for("Technology") == "growth"
    assert val.archetype_for(None) == "growth"


def test_inappropriate_metric_blocked():
    r = val.check_metric("current_ratio", "Financials")
    assert r["allowed"] is False
    assert "override" in r["reason"]
    # with explicit override + reason → allowed, recorded
    r2 = val.check_metric("current_ratio", "Financials",
                          override=True, override_reason="net lender")
    assert r2["allowed"] is True and r2["overridden"] is True


def test_growth_valuation_math():
    # price 100, eps 5 → P/E 20; EV/EBITDA: mcap 1000 + netdebt 200 = 1200/60 = 20
    out = val.compute(
        "Technology", price=100, shares=10, eps=5, ebitda=60,
        net_debt=Decimal(200), ni_series=[4, 5], fcf_ps=4,
    )
    assert out["metrics"]["pe"] == pytest.approx(20)
    assert out["metrics"]["ev_ebitda"] == pytest.approx(20)
    assert out["metrics"]["p_fcf"] == pytest.approx(25)
    assert "fwd_pe" in out["missing"][0]


def test_financial_valuation():
    out = val.compute("Financials", price=80, shares=10, equity=500,
                      ni_series=[50])
    assert out["metrics"]["bvps"] == pytest.approx(50)
    assert out["metrics"]["pb"] == pytest.approx(1.6)
    assert out["metrics"]["roe_latest"] == pytest.approx(0.1)


def test_commodity_normalized():
    out = val.compute("Energy", price=50, shares=10,
                      ni_series=[20, 40, 60])  # avg 40 → norm eps 4 → p/e 12.5
    assert out["metrics"]["normalized_eps"] == pytest.approx(4)
    assert out["metrics"]["normalized_pe"] == pytest.approx(12.5)
