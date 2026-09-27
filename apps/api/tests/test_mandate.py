from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.models.identity import Role, User
from app.models.mandate import Mandate
from app.services import mandate as svc
from app.services.mandate import MandateIn


def _user() -> User:
    return User(email="a@a.io", display_name="A", password_hash="x")


BASE = {"change_note": "initial test mandate"}


async def test_defaults_create(db):
    body = MandateIn(**BASE)
    m = await svc.create_version(db, body, _user())
    await db.commit()
    assert m.version == 1 and m.is_active
    assert m.investment_split_pct == 70.0
    assert m.inv_min_stocks == 10 and m.inv_max_stocks == 15
    assert m.trading_max_stocks == 5
    assert m.min_win_rate_pct == 60.0
    assert m.min_risk_reward == 1.5
    assert m.green_zone_pass_score == 15  # C1 framework value


def test_split_must_sum_100():
    with pytest.raises(ValidationError):
        MandateIn(investment_split_pct=80, trading_split_pct=30, **BASE)


def test_min_max_stock_bounds():
    with pytest.raises(ValidationError):
        MandateIn(inv_min_stocks=20, inv_max_stocks=10, **BASE)


def test_change_note_required():
    with pytest.raises(ValidationError):
        MandateIn(change_note="")


async def test_versioning_retires_previous(db):
    u = _user()
    m1 = await svc.create_version(db, MandateIn(**BASE), u)
    await db.flush()
    m2 = await svc.create_version(
        db, MandateIn(max_drawdown_pct=12.0, change_note="tighter dd"), u
    )
    await db.commit()

    versions = (await db.execute(select(Mandate))).scalars().all()
    assert len(versions) == 2
    assert m1.is_active is False and m2.is_active is True
    assert m1.max_drawdown_pct == 15.0   # v1 unchanged — immutable
    assert m2.max_drawdown_pct == 12.0


async def test_as_of_returns_correct_version(db):
    u = _user()
    m1 = await svc.create_version(db, MandateIn(**BASE), u)
    await db.flush()
    # v2 effective tomorrow — not yet in force
    tomorrow = datetime.now(UTC) + timedelta(days=1)
    m2 = await svc.create_version(
        db, MandateIn(change_note="future", effective_from=tomorrow), u
    )
    await db.commit()

    # get_active ignores v2 until effective_from
    active = await svc.get_active(db)
    assert active.version == m1.version

    hist = await svc.as_of(db, datetime.now(UTC) + timedelta(days=2))
    assert hist.version == m2.version


async def test_decision_snapshots_mandate_version(db):
    """Historical decisions keep the mandate they were decided under."""
    from app.models.governance import DecisionRecord
    from app.models.instruments import Instrument

    u = _user()
    m1 = await svc.create_version(db, MandateIn(**BASE), u)
    await db.flush()
    inst = Instrument(symbol="AAA", name="T")
    db.add(inst)
    await db.flush()
    d = DecisionRecord(
        instrument_id=inst.id, stage="approved",
        verdict="approve", mandate_version=m1.version,
    )
    db.add(d)
    await svc.create_version(
        db, MandateIn(max_drawdown_pct=8.0, change_note="update"), u
    )
    await db.commit()
    assert d.mandate_version == 1  # unchanged by later mandate
