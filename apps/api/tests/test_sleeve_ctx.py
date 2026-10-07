"""Sleeve-ledger context (`risk._sleeve_ctx`) regressions — the Risk
Center book must mirror the maintenance engine:

  - candidates (watchlist / trade_eligible) carry a hypothetical
    starter size but are NOT money at risk → excluded from gross,
    margin, positions_used
  - P&L uses per-leg fill accounting, not (px − entry) × shares
  - the displayed stop floor is the TIGHTER of equity stop and gross
    stop, with the binding rule exposed
"""
import pytest
from datetime import datetime, timedelta, timezone

from app.models.instruments import Instrument
from app.models.market import OhlcvBar
from app.models.risk import PyramidTradeRec, SleeveState
from app.routers.risk import _sleeve_ctx
from app.services import risk_engine as re_

NOW = datetime.now(timezone.utc)


def _limits(**over):
    vals = {"sleeve_enabled": True, "sleeve_pct": 0.30,
            "sleeve_target_leverage": 5.0, "sleeve_initial_margin": 0.20,
            "sleeve_max_positions": 5, "sleeve_portfolio_stop_pct": 0.20,
            "sleeve_gross_stop_pct": 0.04}
    vals.update(over)
    return re_.Limits(values=vals)


async def _inst(db, sym="AAA"):
    inst = Instrument(symbol=sym, name=sym, asset_class="equity",
                      currency="USD")
    db.add(inst)
    await db.flush()
    return inst


async def _close(db, inst, px):
    db.add(OhlcvBar(instrument_id=inst.id, timeframe="1d", time=NOW,
                    open=px, high=px + 1, low=px - 1, close=px,
                    volume=1e6, source="yahoo"))
    await db.flush()


def _rec(inst, **kw):
    d = dict(instrument_id=inst.id, state="initial_position",
             entry=100.0, atr_initial=10.0, shares=10, stop=85.0,
             target1=130.0, t2_policy="trailing", additions=0,
             engine_version="risk-pyramid/v2.0", events=[],
             params={"leg_shares": 10})
    d.update(kw)
    return PyramidTradeRec(**d)


async def test_candidates_are_not_positions(db):
    """trade_eligible/watchlist seeds carry hypothetical sizing — they
    must not inflate gross, margin, P&L or the positions-used count
    (doc: max-5 applies to real positions; seeds are opportunities)."""
    inst = await _inst(db)
    await _close(db, inst, 110.0)
    db.add(_rec(inst, state="trade_eligible", shares=50))
    db.add(_rec(inst, state="watchlist", shares=25))
    await db.commit()
    sleeve = await _sleeve_ctx(db, {"nav": 100_000}, _limits())
    st = sleeve["state"]
    assert st["gross"] == 0.0
    assert st["used_margin"] == 0.0
    assert st["positions_used"] == 0
    assert sleeve["positions"] == []
    assert st["unrealized_pnl"] == 0.0


async def test_real_position_marks_to_last_close(db):
    inst = await _inst(db)
    await _close(db, inst, 120.0)
    db.add(_rec(inst, shares=10, entry=100.0))
    await db.commit()
    sleeve = await _sleeve_ctx(db, {"nav": 100_000}, _limits())
    st = sleeve["state"]
    assert st["gross"] == pytest.approx(1_200.0)
    assert st["positions_used"] == 1
    assert st["unrealized_pnl"] == pytest.approx(200.0)
    assert sleeve["positions"][0]["symbol"] == "AAA"


async def test_per_leg_basis_pnl_after_add(db):
    """Two legs (10@100 + 10@130), last close 140 → real P&L is
    2,800 − 2,300 = 500. The naive (px − entry) × shares would
    overstate it at 800."""
    inst = await _inst(db)
    await _close(db, inst, 140.0)
    db.add(_rec(inst, shares=20, additions=1, params={
        "leg_shares": 10,
        "leg_fills": [{"fill": 100.0, "shares": 10, "leg": 1},
                      {"fill": 130.0, "shares": 10, "leg": 2}]}))
    await db.commit()
    sleeve = await _sleeve_ctx(db, {"nav": 100_000}, _limits())
    assert sleeve["state"]["unrealized_pnl"] == pytest.approx(500.0)


async def test_gross_stop_binds_before_equity_stop(db):
    """Fully pyramided book: 4% of gross < 20% of sleeve equity → the
    gross floor is what the engine enforces, and the dashboard must
    say so — same floor as pyramid_maintain."""
    inst = await _inst(db)
    await _close(db, inst, 200.0)
    # 500 × $200 = $100k gross on $30k sleeve equity
    db.add(_rec(inst, shares=500, entry=200.0))
    await db.commit()
    sleeve = await _sleeve_ctx(db, {"nav": 100_000}, _limits())
    st = sleeve["state"]
    # equity stop = 20% × 30k = 6k; gross stop = 4% × 100k = 4k
    assert sleeve["stop_floor_binding"] == "gross_stop"
    assert sleeve["stop_floor_usd"] == pytest.approx(4_000.0)
    assert sleeve["distance_to_portfolio_stop_usd"] \
        == pytest.approx(4_000.0)


async def test_drawdown_uses_equity_high_water(db):
    """Persisted HWM $30.5k, current mark $30k → $500 drawdown from
    the ratchet, not from stored drawdown_pct."""
    inst = await _inst(db)
    await _close(db, inst, 200.0)
    db.add(_rec(inst, shares=500, entry=200.0))   # $100k gross
    db.add(SleeveState(state="active", equity_hwm=30_500.0))
    await db.commit()
    sleeve = await _sleeve_ctx(db, {"nav": 100_000}, _limits())
    st = sleeve["state"]
    assert st["drawdown_usd"] == pytest.approx(500.0)
    assert st["drawdown_pct"] == pytest.approx(500.0 / 30_000.0)
    # gross floor 4k binds (any open book at ≤5X); 4k − 500 = 3.5k left
    assert sleeve["stop_floor_binding"] == "gross_stop"
    assert sleeve["distance_to_portfolio_stop_usd"] \
        == pytest.approx(3_500.0)
