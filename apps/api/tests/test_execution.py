"""Part 30 — execution FSM, idempotency, safety controls."""

import pytest
from sqlalchemy import select

from app.models.execution import BrokerOrderRec, KillSwitch
from app.models.governance import OrderTicket
from app.providers.broker import PaperBroker, BrokerOrder
from app.services import execution as ex
from app.services.approvals import params_hash


def _ticket(db_id="tk1", approved=True, **over):
    t = OrderTicket(
        id=db_id, decision_id="d1", instrument_id="i1",
        side="buy", order_type="market", quantity=100,
        limit_price=None, est_notional=10_000,
        stop=90, target1=140, reward_risk=4.0,
        status="approved" if approved else "proposed",
        params_hash=params_hash("buy", 100, None, 90, 140),
        risk_snapshot={}, exposure_before={}, exposure_after={},
        risk_policy_version="risk-pyramid/v1.0")
    for k, v in over.items():
        setattr(t, k, v)
    return t


@pytest.mark.asyncio
async def test_full_lifecycle_paper_fill(db):
    from app.providers import broker as broker_mod
    broker_mod.PAPER = PaperBroker(cash=1_000_000)
    broker_mod.PAPER.set_quote("NVDA", 225.0)
    t = _ticket()
    res = await ex.submit_ticket(db, t, symbol="NVDA")
    assert res["status"] == "filled"
    assert res["avg_fill"] == 225.0
    assert res["commission"] == pytest.approx(0.50)
    rec = await db.get(BrokerOrderRec, res["order_id"])
    assert rec.broker_order_id.startswith("P")
    assert t.status == "filled"


@pytest.mark.asyncio
async def test_idempotency_dedupes(db):
    from app.providers import broker as broker_mod
    broker_mod.PAPER = PaperBroker(cash=1_000_000)
    broker_mod.PAPER.set_quote("NVDA", 225.0)
    t = _ticket()
    r1 = await ex.submit_ticket(db, t, symbol="NVDA")
    t2 = _ticket()  # same params_hash → same idem key
    r2 = await ex.submit_ticket(db, t2, symbol="NVDA")
    assert r2["deduped"] is True
    assert r2["order_id"] == r1["order_id"]


@pytest.mark.asyncio
async def test_unapproved_ticket_refused(db):
    t = _ticket(approved=False)
    with pytest.raises(PermissionError, match="approved"):
        await ex.submit_ticket(db, t, symbol="NVDA")


@pytest.mark.asyncio
async def test_stale_params_refused(db):
    t = _ticket()
    t.quantity = 999           # tamper after approval → hash mismatch
    with pytest.raises(PermissionError, match="params changed"):
        await ex.submit_ticket(db, t, symbol="NVDA")


@pytest.mark.asyncio
async def test_kill_switch_blocks(db):
    await ex.set_kill_switch(db, True, "u1", "test halt")
    t = _ticket()
    with pytest.raises(PermissionError, match="kill switch"):
        await ex.submit_ticket(db, t, symbol="NVDA")
    await ex.set_kill_switch(db, False, "u1", "clear")


@pytest.mark.asyncio
async def test_notional_cap(db):
    t = _ticket()
    t.est_notional = 500_000   # > $100k cap
    with pytest.raises(PermissionError, match="cap"):
        await ex.submit_ticket(db, t, symbol="NVDA")


@pytest.mark.asyncio
async def test_disconnect_marks_unknown_never_retries(db):
    from app.providers import broker as broker_mod
    broker_mod.PAPER = PaperBroker(cash=1_000_000)
    broker_mod.PAPER.connected = False
    t = _ticket()
    res = await ex.submit_ticket(db, t, symbol="NVDA")
    assert res["status"] == "unknown"
    rec = await db.get(BrokerOrderRec, res["order_id"])
    assert "uncertain" in rec.reject_reason
    broker_mod.PAPER.connected = True


@pytest.mark.asyncio
async def test_broker_reject_insufficient_cash(db):
    from app.providers import broker as broker_mod
    broker_mod.PAPER = PaperBroker(cash=1_000)   # can't afford 100×225
    broker_mod.PAPER.set_quote("NVDA", 225.0)
    t = _ticket()
    res = await ex.submit_ticket(db, t, symbol="NVDA")
    assert res["status"] == "rejected"
    assert t.status == "proposed"    # back for re-approval


def test_ibkr_refuses_unconfigured():
    from app.providers.broker import IbkrAdapter
    from app.providers.base import ProviderConfigError
    with pytest.raises(ProviderConfigError):
        IbkrAdapter()


def test_alpaca_requires_keys():
    from app.providers.broker import get_adapter
    from app.providers.base import ProviderConfigError
    import os
    os.environ.pop("ALPACA_API_KEY", None)
    os.environ.pop("ALPACA_SECRET_KEY", None)
    with pytest.raises(ProviderConfigError, match="ALPACA_API_KEY"):
        get_adapter("alpaca")


def test_paper_capabilities_honest():
    p = PaperBroker()
    cap = p.capabilities()
    assert cap["mode"] == "simulated" and cap["orders"] is True


@pytest.mark.asyncio
async def test_cancel_filled_is_noop(db):
    from app.providers import broker as broker_mod
    broker_mod.PAPER = PaperBroker(cash=1_000_000)
    broker_mod.PAPER.set_quote("NVDA", 225.0)
    t = _ticket()
    res = await ex.submit_ticket(db, t, symbol="NVDA")
    rec = await db.get(BrokerOrderRec, res["order_id"])
    r = await ex.cancel(db, rec)
    assert r["status"] == "filled"     # terminal — no phantom cancel


@pytest.mark.asyncio
async def test_limit_not_marketable_rests(db):
    from app.providers import broker as broker_mod
    from app.providers.broker import PaperBroker
    p = broker_mod.PAPER = PaperBroker(cash=1_000_000)
    p.set_quote("NVDA", 225.0)
    t = _ticket(order_type="limit", limit_price=200.0)
    t.params_hash = params_hash("buy", 100, 200.0, 90, 140)
    res = await ex.submit_ticket(db, t, symbol="NVDA")
    assert res["status"] == "acknowledged"  # limit below mkt → rests
