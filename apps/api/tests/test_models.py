from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

import app.models  # noqa: F401 — ensure metadata loaded
from app.db.pointintime import latest_as_of
from app.models.fundamentals import FundamentalObservation
from app.models.instruments import Instrument
from app.models.mandate import Mandate
from app.models.portfolio import LedgerEntry, Portfolio


async def _inst(db) -> Instrument:
    i = Instrument(symbol="NVDA", name="NVIDIA")
    db.add(i)
    await db.flush()
    return i


async def test_fundamental_observation_provenance(db):
    inst = await _inst(db)
    obs = FundamentalObservation(
        instrument_id=inst.id,
        concept="us-gaap:Revenue",
        value=Decimal("60922000000"),
        unit="USD",
        currency="USD",
        period_end=date(2024, 1, 28),
        fiscal_period="FY",
        observed_at=datetime(2024, 1, 28, tzinfo=UTC),
        published_at=datetime(2024, 2, 21, tzinfo=UTC),
        source="edgar",
        source_ref="0001045810-24-000029",
    )
    db.add(obs)
    await db.commit()
    assert obs.ingested_at is not None
    assert obs.quality == "ok"


async def test_point_in_time_excludes_future_publications(db):
    inst = await _inst(db)
    filed = datetime(2024, 2, 21, tzinfo=UTC)
    db.add(
        FundamentalObservation(
            instrument_id=inst.id,
            concept="us-gaap:Revenue",
            value=Decimal("100"),
            period_end=date(2024, 1, 28),
            observed_at=date(2024, 1, 28),
            published_at=filed,
            source="edgar",
        )
    )
    await db.commit()

    asof_before = filed - timedelta(days=1)
    asof_after = filed + timedelta(days=1)

    assert await latest_as_of(
        db, FundamentalObservation, asof_before,
        instrument_id=inst.id, concept="us-gaap:Revenue",
    ) is None
    got = await latest_as_of(
        db, FundamentalObservation, asof_after,
        instrument_id=inst.id, concept="us-gaap:Revenue",
    )
    assert got is not None and got.value == Decimal("100")


async def test_restatement_never_overwrites(db):
    inst = await _inst(db)
    filed = datetime(2024, 2, 21, tzinfo=UTC)
    v1 = FundamentalObservation(
        instrument_id=inst.id, concept="us-gaap:Revenue",
        value=Decimal("100"), period_end=date(2024, 1, 28),
        observed_at=date(2024, 1, 28), published_at=filed, source="edgar",
    )
    db.add(v1)
    await db.flush()
    v2 = FundamentalObservation(
        instrument_id=inst.id, concept="us-gaap:Revenue",
        value=Decimal("110"), period_end=date(2024, 1, 28),
        observed_at=date(2024, 1, 28),
        published_at=filed + timedelta(days=30), source="edgar",
        supersedes_id=v1.id, quality="restated",
    )
    db.add(v2)
    await db.commit()

    before = await latest_as_of(
        db, FundamentalObservation, filed + timedelta(days=1),
        instrument_id=inst.id, concept="us-gaap:Revenue",
    )
    after = await latest_as_of(
        db, FundamentalObservation, filed + timedelta(days=31),
        instrument_id=inst.id, concept="us-gaap:Revenue",
    )
    assert before.value == Decimal("100")  # as-of sees original
    assert after.value == Decimal("110")   # later view sees restatement


async def test_ledger_uses_numeric_not_float(db):
    p = Portfolio(name="Investment", kind="investment")
    db.add(p)
    await db.flush()
    db.add(
        LedgerEntry(
            portfolio_id=p.id, kind="deposit",
            amount=Decimal("100000.12345678"), currency="USD",
        )
    )
    await db.commit()
    total = (
        await db.execute(
            select(func.sum(LedgerEntry.amount)).where(
                LedgerEntry.portfolio_id == p.id
            )
        )
    ).scalar()
    assert total == Decimal("100000.12345678")


async def test_mandate_is_versioned(db):
    v1 = Mandate(version=1, is_active=True, max_drawdown_pct=15.0)
    db.add(v1)
    await db.commit()
    v2 = Mandate(version=2, is_active=True, max_drawdown_pct=12.0)
    db.add(v2)
    await db.commit()
    rows = (await db.execute(select(Mandate))).scalars().all()
    assert len(rows) == 2  # v1 retained, not mutated
