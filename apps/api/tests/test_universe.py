import pytest

from app.models.instruments import Instrument, InstrumentIdentifier
from app.models.universe import Universe
from app.services import universe as svc
from app.services.universe import DEFAULT_RULES


def _inst(sym, **kw) -> Instrument:
    i = Instrument(symbol=sym, name=sym, **kw)
    return i


def test_eligibility_blocks_delisted():
    i = _inst("DEL", listing_status="delisted",
              market_cap=10**10, avg_dollar_volume_30d=10**7)
    reasons = svc.eligibility_reasons(i, DEFAULT_RULES)
    assert any("listing_status" in r for r in reasons)


def test_eligibility_blocks_unknown_data():
    i = _inst("NODATA")
    reasons = svc.eligibility_reasons(i, DEFAULT_RULES)
    assert "market_cap unknown" in reasons
    assert "liquidity unknown" in reasons


def test_eligibility_blocks_low_liquidity_and_cap():
    i = _inst("TINY", market_cap=100_000_000, avg_dollar_volume_30d=1_000)
    reasons = svc.eligibility_reasons(i, DEFAULT_RULES)
    assert any("market_cap" in r and "<" in r for r in reasons)
    assert any("adv30" in r for r in reasons)


def test_eligibility_blocks_asset_class():
    i = _inst("BTC", asset_class="crypto",
              market_cap=10**12, avg_dollar_volume_30d=10**9)
    reasons = svc.eligibility_reasons(i, DEFAULT_RULES)
    assert any("asset_class" in r for r in reasons)


def test_eligible_passes():
    i = _inst("NVDA", market_cap=3_000_000_000_000,
              avg_dollar_volume_30d=10_000_000_000)
    assert svc.eligibility_reasons(i, DEFAULT_RULES) == []


async def test_search_and_detail(db):
    big = _inst("NVDA", market_cap=3e12, avg_dollar_volume_30d=1e10)
    db.add(big)
    tiny = _inst("TINY", market_cap=1e8, avg_dollar_volume_30d=1e3)
    db.add(tiny)
    dead = _inst("GONE", listing_status="delisted")
    db.add(dead)
    await db.commit()

    all_r = await svc.search_instruments(db, include_inactive=True)
    syms = {r["symbol"] for r in all_r}
    assert syms == {"NVDA", "TINY", "GONE"}

    active_only = await svc.search_instruments(db)
    assert "GONE" not in {r["symbol"] for r in active_only}

    elig = await svc.search_instruments(db, eligible_only=True)
    assert [r["symbol"] for r in elig] == ["NVDA"]

    det = await svc.instrument_detail(db, big)
    assert det["eligibility"]["research_eligible"] is True
    assert det["approved_for_trading"] is False  # no approved membership yet


async def test_membership_exclusion_keeps_reason(db):
    u = Universe(name="approved", tier="approved")
    i = _inst("ACN")
    db.add_all([u, i])
    await db.commit()

    m = await svc.set_membership(
        db, universe_name="approved", instrument=i, status="active"
    )
    assert m.status == "active"
    m2 = await svc.set_membership(
        db, universe_name="approved", instrument=i,
        status="excluded", reason="concentration breach",
    )
    assert m2.status == "excluded" and m2.reason == "concentration breach"


async def test_universe_stats(db):
    g = Universe(name="global", tier="global")
    e = Universe(name="eligible", tier="eligible")
    a = Universe(name="approved", tier="approved")
    i = _inst("NVDA")
    db.add_all([g, e, a, i])
    await db.commit()
    for u in (g, e, a):
        await svc.set_membership(
            db, universe_name=u.name, instrument=i, status="active"
        )
    stats = await svc.universe_stats(db)
    assert stats == {"global": 1, "eligible": 1, "approved": 1, "securities": 1}
