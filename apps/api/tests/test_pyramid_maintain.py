"""Per-close pyramid maintenance — the Phase-4 loop: ATR ratchet,
target adds behind earn-the-right re-checks, stop exits, stale-data
skips, sleeve recompute."""
import pytest
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.instruments import Instrument
from app.models.market import OhlcvBar
from app.models.ops import Alert
from app.models.portfolio import LedgerEntry, Portfolio
from app.models.risk import LimitConfig, PyramidTradeRec, RiskCheck
from app.services import pyramid_maintain as pm

NOW = datetime.now(timezone.utc)


async def _bars(db, inst, closes, atr_width=5.0, days_old=0):
    """Daily bars ending `days_old` days ago; H/L ±atr_width of close."""
    n = len(closes)
    for i, c in enumerate(closes):
        t = NOW - timedelta(days=(n - 1 - i) + days_old)
        db.add(OhlcvBar(
            instrument_id=inst.id, timeframe="1d", time=t,
            open=c - 1, high=c + atr_width, low=c - atr_width,
            close=c, volume=1e6, source="yahoo"))
    await db.flush()


def _rec(inst, **kw):
    d = dict(instrument_id=inst.id, state="initial_position",
             entry=100.0, atr_initial=10.0, shares=10, stop=85.0,
             target1=130.0, t2_policy="trailing",
             additions=0, engine_version="risk-pyramid/v2.0",
             events=[], params={"leg_shares": 10})
    d.update(kw)
    return PyramidTradeRec(**d)


async def _funded_book(db):
    """Portfolio + $100k cash so the risk gate has a real NAV."""
    pf = Portfolio(name="p", kind="trading")
    db.add(pf)
    await db.flush()
    db.add(LedgerEntry(portfolio_id=pf.id, kind="deposit",
                       amount=100_000, currency="USD"))
    await db.flush()


async def test_ratchet_tightens_stop(db):
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    # price climbs +1/day to 118, below the 130 target; TR stays 10 →
    # new stop 118 − 15 = 103 > 85 → ratchet
    await _bars(db, inst, [100] * 40 + list(range(101, 119)))
    rec = _rec(inst)
    db.add(rec)
    await db.flush()

    out = await pm.maintain_open_pyramids(db)
    assert out["processed"] == 1
    assert out["tightened"] == 1
    assert rec.stop == pytest.approx(118 - 15)
    assert rec.state == "initial_position"
    assert any("maintenance: stop" in e for e in rec.events)


async def test_stop_never_widens(db):
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    # price dips toward the stop but stays above → candidate new stop
    # 90 − 15 = 75 < existing 85 → must NOT move down
    await _bars(db, inst, [100] * 40 + [90] * 3)
    rec = _rec(inst)
    db.add(rec)
    await db.flush()

    out = await pm.maintain_open_pyramids(db)
    assert rec.stop == pytest.approx(85)
    assert out["tightened"] == 0


async def test_stop_breach_exits_all(db):
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    await _bars(db, inst, [100] * 40 + [80] * 3)   # close < 85 stop
    rec = _rec(inst)
    db.add(rec)
    await db.flush()

    out = await pm.maintain_open_pyramids(db)
    assert rec.state == "stopped_out"
    assert out["stopped"] == 1
    alerts = (await db.execute(select(Alert))).scalars().all()
    assert any("STOPPED" in a.message for a in alerts)


async def test_target_hit_adds_leg_after_risk_recheck(db):
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    await _funded_book(db)
    db.add(LimitConfig(version=1, payload={"min_sectors": 0}))
    # +1/day ramp to 135 keeps TR at 10 → next target = 135 + 30
    await _bars(db, inst, [100] * 40 + list(range(101, 136)))
    rec = _rec(inst)
    db.add(rec)
    await db.flush()

    out = await pm.maintain_open_pyramids(db)
    assert out["added"] == 1
    assert rec.shares == 20
    assert rec.additions == 1
    # next target = close + 3×ATR(10) — the doc's moving ladder
    assert rec.target1 == pytest.approx(135 + 30)
    # earn-the-right re-check is persisted — the audit trail
    checks = (await db.execute(select(RiskCheck))).scalars().all()
    assert len(checks) == 1 and checks[0].allowed is True


async def test_target_hit_blocked_when_gate_fails(db):
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    # no portfolio → nav ≈ 0 → cash/leverage gates deny the add
    await _bars(db, inst, [100] * 40 + [135] * 3)
    rec = _rec(inst)
    db.add(rec)
    await db.flush()

    out = await pm.maintain_open_pyramids(db)
    assert out["added"] == 0
    assert rec.shares == 10                 # no leg was granted
    assert rec.state == "initial_position"  # pyramid keeps trailing
    checks = (await db.execute(select(RiskCheck))).scalars().all()
    assert len(checks) == 1 and checks[0].allowed is False


async def test_stale_bars_skip_maintenance(db):
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    await _bars(db, inst, [100] * 43, days_old=10)  # last bar 10d old
    rec = _rec(inst)
    db.add(rec)
    await db.flush()

    out = await pm.maintain_open_pyramids(db)
    assert out["stale_skipped"] == 1
    assert rec.stop == pytest.approx(85)     # untouched
    assert rec.state == "initial_position"
    assert any("stale" in e for e in rec.events)


async def test_seed_records_not_maintained(db):
    """watchlist/trade_eligible seeds are candidates, not positions —
    the loop must not advance them."""
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    await _bars(db, inst, [100] * 40 + [140] * 3)
    rec = _rec(inst, state="trade_eligible", shares=0)
    db.add(rec)
    await db.flush()

    out = await pm.maintain_open_pyramids(db)
    assert out["processed"] == 0
    assert rec.state == "trade_eligible"


async def test_portfolio_stop_liquidates_and_cools_down(db):
    """20% sleeve drawdown → liquidate all + cooldown (absolute)."""
    from app.models.risk import SleeveState
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    await _funded_book(db)                    # nav 100k → sleeve 30k
    db.add(LimitConfig(version=1, payload={
        "sleeve_enabled": True, "min_sectors": 0}))
    # entry 100 → close 60: stop set low (40) so the breach is the
    # PORTFOLIO stop, not the per-trade stop — 1000sh × −40 = −40k
    # vs $30k sleeve equity → −133% ≥ 20%
    await _bars(db, inst, [100] * 40 + [60] * 3)
    rec = _rec(inst, shares=1000, stop=40.0,
               params={"leg_shares": 1000})
    db.add(rec)
    await db.flush()

    out = await pm.maintain_open_pyramids(db)
    assert out["liquidated"] == 1
    assert rec.state == "closed"
    assert any("PORTFOLIO STOP" in e for e in rec.events)
    life = (await db.execute(select(SleeveState))).scalar_one()
    assert life.state == "cooldown"
    assert "drawdown" in (life.cooldown_reason or "")
    alerts = (await db.execute(select(Alert))).scalars().all()
    assert any("PORTFOLIO STOP" in a.message.upper()
               for a in alerts)


async def test_cooldown_survives_next_sweep(db):
    """Once in cooldown a later sweep does not re-liquidate — and the
    gate stack keeps new orders out until release."""
    from app.models.risk import SleeveState
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    await _funded_book(db)
    db.add(LimitConfig(version=1, payload={
        "sleeve_enabled": True, "min_sectors": 0}))
    db.add(SleeveState(state="cooldown",
                       cooldown_reason="test"))
    await _bars(db, inst, [100] * 43)
    db.add(_rec(inst))
    await db.flush()

    out = await pm.maintain_open_pyramids(db)
    assert out.get("liquidated", 0) == 0
    assert out["sleeve_cooldown"] is True

    # check_order sees the cooldown through pf["sleeve"]["cooldown"]
    from app.services import risk_engine as re_
    cfg = re_.sleeve_config(re_.Limits(values={"sleeve_enabled": True}))
    st = re_.sleeve_state(100_000, 0, cfg)
    pf = {"nav": 100_000, "cash": 100_000, "positions": [],
          "sleeve": {"enabled": True, "config": cfg, "state": st,
                     "positions": [], "cooldown": True}}
    gate = re_.check_order(
        {"side": "buy", "symbol": "TEST", "sector": None,
         "notional": 1_000, "sleeve": True}, pf,
        limits=re_.Limits(values={"min_sectors": 0}))
    assert gate["allowed"] is False
    assert any(b["rule"] == "sleeve_cooldown"
               for b in gate["breaches"])


async def test_summary_shape(db):
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    await _bars(db, inst, [100] * 43)
    db.add(_rec(inst))
    await db.flush()
    out = await pm.maintain_open_pyramids(db)
    assert set(out) >= {"processed", "tightened", "added", "stopped",
                        "add_blocked", "stale_skipped", "actions",
                        "sleeve", "maintain_version"}
