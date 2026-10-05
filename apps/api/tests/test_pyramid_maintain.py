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


async def test_stop_breach_proposes_exit_never_closes(db):
    """Stop breach queues an exit_request — the position stays open
    until a human approves. No close is ever automatic."""
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    await _bars(db, inst, [100] * 40 + [80] * 3)   # close < 85 stop
    rec = _rec(inst)
    db.add(rec)
    await db.flush()

    out = await pm.maintain_open_pyramids(db)
    assert rec.state == "initial_position"        # still open
    assert out["exits_proposed"] == 1
    assert out["stopped"] == 0
    req = rec.params["exit_request"]
    assert req["trigger"] == "stop_breach"
    assert req["proposed_state"] == "stopped_out"
    alerts = (await db.execute(select(Alert))).scalars().all()
    assert any("EXIT APPROVAL REQUIRED" in a.message
               for a in alerts)

    # a second sweep doesn't re-fire — it waits on the human
    out2 = await pm.maintain_open_pyramids(db)
    assert out2["exit_pending"] == 1
    assert out2["exits_proposed"] == 0


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


async def test_portfolio_stop_queues_exits_and_cools_down(db):
    """20% sleeve drawdown → cooldown NOW (no new risk) + every
    position queued for human-approved liquidation — closes are
    never automatic."""
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
    assert out["liquidation_queued"] == 1
    assert rec.state == "initial_position"     # open pending approval
    req = rec.params["exit_request"]
    assert req["trigger"] == "portfolio_stop"
    assert any("EXIT PROPOSED" in e for e in rec.events)
    life = (await db.execute(select(SleeveState))).scalar_one()
    assert life.state == "cooldown"
    assert "drawdown" in (life.cooldown_reason or "")
    await db.refresh(inst)
    assert inst.status == "cooldown"
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
    assert out.get("liquidation_queued", 0) == 0
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


async def test_per_leg_cost_basis_pnl(db):
    """Legs added at target prices are P&L'd at THEIR fill, not the
    leg-1 entry — 10sh@100 + 10sh@130 marked at 120 = +$100, not
    +$400."""
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    await _funded_book(db)
    db.add(LimitConfig(version=1, payload={"sleeve_enabled": True,
                                           "min_sectors": 0}))
    await _bars(db, inst, [120] * 43)
    rec = _rec(inst, shares=20, additions=1, target1=200.0,
               params={"leg_shares": 10,
                       "leg_fills": [{"fill": 100.0, "shares": 10,
                                      "leg": 1},
                                     {"fill": 130.0, "shares": 10,
                                      "leg": 2}]})
    db.add(rec)
    await db.flush()

    out = await pm.maintain_open_pyramids(db)
    # 20sh × 120 = 2400 marked − (1000 + 1300) cost = +100
    assert out["sleeve"]["sleeve_open_pnl"] == pytest.approx(100.0)


async def test_legacy_fills_reconstruct_at_entry(db):
    """Rows without leg_fills degrade to one leg at entry — the old
    (wrong-after-adds) behaviour, flagged as reconstructed."""
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    rec = _rec(inst, shares=20, additions=1)
    fills = pm._leg_fills(rec)
    assert fills == [{"fill": 100.0, "shares": 20, "leg": 1,
                      "reconstructed": True}]
    assert pm._cost_basis(rec) == pytest.approx(2000.0)


async def test_drawdown_measured_from_high_water(db):
    """Equity that peaked and gave back counts as drawdown — gains
    surrendered are losses taken. HWM 40k → mark 28k = $12k dd."""
    from app.models.risk import SleeveState
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    await _funded_book(db)                    # sleeve equity 30k
    db.add(LimitConfig(version=1, payload={
        "sleeve_enabled": True, "min_sectors": 0,
        "sleeve_gross_stop_pct": 0.0}))       # isolate the HWM rule
    db.add(SleeveState(state="active", equity_hwm=40_000.0))
    # 100sh @98 vs entry 100 → pnl −200 → eq_mark 29.8k; vs HWM 40k
    # → $10.2k ≥ $6k stop → liquidate. Under the old rule (−0.67%
    # from cost basis) it survived.
    await _bars(db, inst, [98] * 43)
    db.add(_rec(inst, shares=100, stop=1.0, target1=9999.0,
                params={"leg_shares": 100}))
    await db.flush()

    out = await pm.maintain_open_pyramids(db)
    assert out.get("liquidation_queued") == 1
    life = (await db.execute(select(SleeveState))).scalar_one()
    assert life.state == "cooldown"
    assert life.drawdown_pct == pytest.approx(10_200 / 30_000)


async def test_gross_stop_fires_before_equity_stop(db):
    """4%-of-current-gross is the tighter floor at low deployment:
    100sh down $5 = $500 loss < $6k equity stop, but ≥ $380 = 4% of
    the $9.5k gross → liquidate."""
    from app.models.risk import SleeveState
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    await _funded_book(db)
    db.add(LimitConfig(version=1, payload={
        "sleeve_enabled": True, "min_sectors": 0}))
    await _bars(db, inst, [95] * 43)
    db.add(_rec(inst, shares=100, stop=1.0, target1=9999.0,
                params={"leg_shares": 100}))
    await db.flush()

    out = await pm.maintain_open_pyramids(db)
    assert out.get("liquidation_queued") == 1
    assert "gross-exposure" in (await db.execute(
        select(SleeveState))).scalar_one().cooldown_reason


async def _weak_fundamentals(db, inst):
    """4y growing revenue + persistently weak ROIC — enough data to
    evaluate (has_fund), but moat fails → four_ms fails."""
    from datetime import date
    from app.models.fundamentals import FundamentalObservation as FO
    for i, yr in enumerate(range(2022, 2026)):
        end = date(yr, 1, 31)
        for concept, val in [
                ("us-gaap:Revenues", 1_000 * 1.2 ** i),
                ("us-gaap:OperatingIncomeLoss", 40.0),
                ("us-gaap:IncomeTaxExpenseBenefit", 8.0),
                ("us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", 48.0),
                ("us-gaap:WeightedAverageNumberOfSharesOutstandingBasic", 100.0)]:
            db.add(FO(instrument_id=inst.id, concept=concept, value=val,
                      unit="USD", period_start=date(yr - 1, 2, 1),
                      period_end=end, fiscal_period="FY",
                      observed_at=NOW, published_at=NOW,
                      source="edgar", source_ref=f"t-{concept}-{yr}"))
        for concept, val in [("us-gaap:StockholdersEquity", 1_000.0),
                             ("us-gaap:LongTermDebt", 100.0),
                             ("us-gaap:CashAndCashEquivalentsAtCarryingValue", 50.0)]:
            db.add(FO(instrument_id=inst.id, concept=concept, value=val,
                      unit="USD", period_start=None, period_end=end,
                      fiscal_period="FY", observed_at=NOW,
                      published_at=NOW, source="edgar",
                      source_ref=f"t-{concept}-{yr}"))
    await db.flush()


async def test_fundamental_deterioration_proposes_exit(db):
    """Doc Step 20B — a position that was TRADE_ELIGIBLE at entry and
    now fails four_ms gets an exit proposal: deterioration =
    pass → fail. Approval still belongs to a human."""
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    await _funded_book(db)
    db.add(LimitConfig(version=1, payload={"sleeve_enabled": True,
                                           "min_sectors": 0}))
    await _weak_fundamentals(db, inst)
    await _bars(db, inst, [110] * 43)
    db.add(_rec(inst, params={"leg_shares": 10,
                              "eligibility": "TRADE_ELIGIBLE"}))
    await db.flush()

    out = await pm.maintain_open_pyramids(db)
    rec = (await db.execute(select(PyramidTradeRec))).scalar_one()
    assert rec.state == "initial_position"     # open pending approval
    assert out["exits_proposed"] == 1
    assert rec.params["exit_request"]["trigger"] == "independent"
    assert any("fundamental deterioration" in e for e in rec.events)


async def test_unqualified_fundamental_fail_alerts_not_exits(db):
    """A position that never passed the gate (no baseline) gets a
    review alert, not an auto-liquidation — no pass→fail transition
    can be established."""
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    await _funded_book(db)
    db.add(LimitConfig(version=1, payload={"sleeve_enabled": True,
                                           "min_sectors": 0}))
    await _weak_fundamentals(db, inst)
    await _bars(db, inst, [110] * 43)
    db.add(_rec(inst))                    # no eligibility baseline
    await db.flush()

    out = await pm.maintain_open_pyramids(db)
    rec = (await db.execute(select(PyramidTradeRec))).scalar_one()
    assert rec.state != "closed"
    assert out["exits_proposed"] == 0
    assert "exit_request" not in (rec.params or {})
    alerts = (await db.execute(select(Alert))).scalars().all()
    assert any("fundamental review" in a.message for a in alerts)


async def test_missing_fundamentals_never_exits(db):
    """Unresearched names can't be condemned by a gate that couldn't
    see them — missing data ≠ deterioration."""
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    await _bars(db, inst, [110] * 43)
    db.add(_rec(inst))
    await db.flush()

    out = await pm.maintain_open_pyramids(db)
    rec = (await db.execute(select(PyramidTradeRec))).scalar_one()
    assert rec.state != "closed"
    assert out["exits_proposed"] == 0


async def test_valuation_exit_proposes_close(db):
    """Doc Step 7/20C — price ≥ sticker is an objective exit signal,
    independent of ATR state or entry qualification. It queues an
    exit_request; a human approves the actual close."""
    from app.models.valuation import ValuationRun
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    await _funded_book(db)
    db.add(LimitConfig(version=1, payload={"sleeve_enabled": True,
                                           "min_sectors": 0}))
    db.add(ValuationRun(
        group_id="g", instrument_id=inst.id, version=1,
        methodology="rule1-dcf/v2.0", inputs={}, outputs={
            "rule1": {"sticker_price": 90.0, "buy_price": 45.0}}))
    await _bars(db, inst, [110] * 43)     # price 110 ≥ sticker 90
    db.add(_rec(inst))
    await db.flush()

    out = await pm.maintain_open_pyramids(db)
    rec = (await db.execute(select(PyramidTradeRec))).scalar_one()
    assert rec.state == "initial_position"
    assert out["exits_proposed"] == 1
    assert "valuation exit" in rec.params["exit_request"]["reason"]
    assert any("EXIT PROPOSED" in e for e in rec.events)


async def test_macro_risk_off_blocks_adds(db):
    """Doc Step 17 — earn-the-right includes current market regime:
    a target hit under risk_off earns no leg."""
    from app.models.macro import RegimeRun
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    await _funded_book(db)
    db.add(LimitConfig(version=1, payload={"sleeve_enabled": True,
                                           "min_sectors": 0}))
    db.add(RegimeRun(as_of=NOW, econ_regime="expansion",
                     market_regime="risk_off", rules_version="r1",
                     features={}, rule_hits=[]))
    # +1/day ramp to 135 keeps TR=10 → target 130 crossed at close 135
    await _bars(db, inst, [100] * 40 + list(range(101, 136)))
    db.add(_rec(inst))
    await db.flush()

    out = await pm.maintain_open_pyramids(db)
    rec = (await db.execute(select(PyramidTradeRec))).scalar_one()
    assert out["added"] == 0
    assert rec.shares == 10
    checks = (await db.execute(select(RiskCheck))).scalars().all()
    assert checks and checks[0].allowed is False
    assert any(b["rule"] == "sleeve_macro_risk_off"
               for b in checks[0].breaches)


async def test_approved_exit_closes_and_marks_exited(db):
    """The human approval path: proposal → POST /pyramid/{id}/exit
    → terminal state + instrument lifecycle EXITED."""
    from app.models.identity import User
    from app.routers.risk import ExitIn, exit_pyramid
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    await _bars(db, inst, [100] * 40 + [80] * 3)   # close < 85 stop
    rec = _rec(inst)
    db.add(rec)
    await db.flush()

    await pm.maintain_open_pyramids(db)
    assert rec.state == "initial_position"     # pending, not closed

    user = User(id="u-test", email="t@t", is_active=True)
    out = await exit_pyramid(rec.id, ExitIn(approve=True), db, user)
    assert out["state"] == "stopped_out"       # proposed_state honored
    assert "exit_request" not in (rec.params or {})
    await db.refresh(inst)
    assert inst.status == "exited"
    assert any("EXIT APPROVED" in e for e in rec.events)


async def test_rejected_exit_clears_request(db):
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    await _bars(db, inst, [100] * 40 + [80] * 3)
    rec = _rec(inst)
    db.add(rec)
    await db.flush()
    await pm.maintain_open_pyramids(db)
    assert "exit_request" in rec.params

    from app.models.identity import User
    from app.routers.risk import ExitIn, exit_pyramid
    user = User(id="u-test", email="t@t", is_active=True)
    out = await exit_pyramid(rec.id, ExitIn(approve=False), db, user)
    assert "exit_request" not in (rec.params or {})
    assert rec.state == "initial_position"
    assert any("EXIT REJECTED" in e for e in rec.events)


async def test_manual_close_is_its_own_approval(db):
    """A human closing directly IS the approval — no pending request
    required."""
    from app.models.identity import User
    from app.routers.risk import ExitIn, exit_pyramid
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    rec = _rec(inst)
    db.add(rec)
    await db.flush()

    user = User(id="u-test", email="t@t", is_active=True)
    out = await exit_pyramid(rec.id, ExitIn(approve=True), db, user)
    assert out["state"] == "closed"
    await db.refresh(inst)
    assert inst.status == "exited"


async def test_weekly_timeframe_processes_closed_week_only(db):
    """Doc Phase 0 — a 1w strategy advances only at completed weekly
    closes; a second sweep in the same week is a no-op."""
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    await _funded_book(db)
    db.add(LimitConfig(version=1, payload={
        "sleeve_enabled": True, "min_sectors": 0,
        "sleeve_atr_timeframe": "1w"}))
    # ~24 weeks of daily bars — enough for a weekly ATR-14
    await _bars(db, inst, [100] * 80 + [110] * 40)
    rec = _rec(inst, target1=9999.0)       # isolate the ratchet
    db.add(rec)
    await db.flush()

    out1 = await pm.maintain_open_pyramids(db)
    assert out1["unchanged_period"] == 0
    period = (rec.params or {}).get("last_tf_period")
    assert period is not None             # a completed week was used
    # same week → nothing new to evaluate
    out2 = await pm.maintain_open_pyramids(db)
    assert out2["unchanged_period"] == 1


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


# ── direction reversal (doc: "for shorts, direction must be
# reversed") ──

async def test_short_ratchet_tightens_down(db):
    """Short pyramid: stop trails ABOVE price, ratchets DOWN only."""
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    # price falls +1/day reversed → 82; new stop 82 + 15 = 97 < 115
    await _bars(db, inst, [100] * 40 + list(range(99, 81, -1)))
    rec = _rec(inst, direction="short", stop=115.0, target1=70.0)
    db.add(rec)
    await db.flush()

    out = await pm.maintain_open_pyramids(db)
    assert out["tightened"] == 1
    assert rec.stop == pytest.approx(82 + 15)
    assert rec.state == "initial_position"


async def test_short_stop_breach_proposes_exit(db):
    """Short stop sits above — a close ≥ stop queues a human-approved
    exit, never an automatic close."""
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    await _bars(db, inst, [100] * 40 + [120] * 3)   # close ≥ 115 stop
    rec = _rec(inst, direction="short", stop=115.0, target1=70.0)
    db.add(rec)
    await db.flush()

    out = await pm.maintain_open_pyramids(db)
    assert out["exits_proposed"] == 1
    assert rec.state == "initial_position"          # still open
    assert rec.params["exit_request"]["trigger"] == "stop_breach"


async def test_short_target_hit_adds_leg(db):
    """A short's target sits BELOW price — hitting it earns a leg."""
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    await _funded_book(db)
    db.add(LimitConfig(version=1, payload={"min_sectors": 0}))
    # ramp down to 65 — through the 70 target
    await _bars(db, inst, [100] * 40 + list(range(99, 64, -1)))
    rec = _rec(inst, direction="short", stop=115.0, target1=70.0)
    db.add(rec)
    await db.flush()

    out = await pm.maintain_open_pyramids(db)
    assert out["added"] == 1
    assert rec.shares == 20
    # next target below the close; the recorded check ran side=sell
    assert rec.target1 == pytest.approx(65 - 30)
    checks = (await db.execute(select(RiskCheck))).scalars().all()
    assert len(checks) == 1 and checks[0].side == "sell"


# ── margin-call reduction — proposals, never autonomous closes ──

async def test_margin_breach_queues_reduction_exits(db):
    """used margin >90% of sleeve equity → the doc's 'reduce before
    the broker calls': exits proposed largest-first until projected
    gross sits under the broker bound."""
    inst = Instrument(symbol="TEST", name="T", asset_class="equity")
    db.add(inst)
    await db.flush()
    await _funded_book(db)          # nav 100k → sleeve eq 30k
    await _bars(db, inst, [100] * 43)
    # maint 50% → broker call at gross = 30k/0.5 = 60k. Two positions
    # at 90k+90k gross → utilisation 180k×20%/30k = 120% > 90%;
    # target = 54k → both must be proposed to get under.
    db.add(LimitConfig(version=1, payload={
        "sleeve_enabled": True, "min_sectors": 0,
        "sleeve_maint_margin": 0.5}))
    big = _rec(inst, shares=900, stop=85.0,
               params={"leg_shares": 900})
    db.add(big)
    inst2 = Instrument(symbol="TST2", name="T2", asset_class="equity")
    db.add(inst2)
    await db.flush()
    await _bars(db, inst2, [100] * 43)
    big2 = _rec(inst2, shares=900, stop=85.0,
                params={"leg_shares": 900})
    db.add(big2)
    await db.flush()
    # force the sleeve positions into maintenance-margin reality
    inst.maintenance_margin_rate = 0.5
    inst2.maintenance_margin_rate = 0.5

    out = await pm.maintain_open_pyramids(db)
    assert out.get("margin_reduction_queued", 0) >= 1
    # all exits proposed, none executed — still open
    assert big.state == "initial_position"
    assert "exit_request" in (big.params or {}) or \
           "exit_request" in (big2.params or {})
