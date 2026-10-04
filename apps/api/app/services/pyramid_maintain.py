"""Per-close pyramid maintenance — the doc's Phase-4 checklist, run
as one job:

    CURRENT ATR
        ↓
    1.5 × ATR  →  tighten-only ratchet (never widen)
        ↓
    target hit → earn-the-right risk re-check → add leg
        ↓
    stop breached → EXIT ALL (state → stopped_out)
        ↓
    sleeve margin + drawdown recomputed → alerts

Every action lands in the record's append-only events log; add
re-checks persist a RiskCheck row — the audit trail for "earn the
right". Stale data BLOCKS stop adjustments and additions (doc data
dependency): a pyramid whose last bar is older than the freshness
window is left untouched and flagged, never maintained on old prices.

Watchlist/trade-eligible seed records are skipped — they describe
candidates, not positions.
"""

from datetime import timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.models.instruments import Instrument
from app.models.market import OhlcvBar
from app.models.risk import (
    LimitConfig, PyramidTradeRec, RiskCheck, SleeveState)
from app.services import qualification as qual
from app.services import risk_engine as re_
from app.services import technical as ti
from app.services.monitoring import emit_alert
from app.services.technical_engine import PARAMS

_MAINTAIN_STATES = (
    "initial_position", "target_1", "position_addition",
    "target_2", "trailing_exit", "partial_exit")

JOB_KEY = "pyramid:maintain"
MAINTAIN_VERSION = "maintain/v1.0"


async def _active_limits(db: AsyncSession) -> re_.Limits:
    cfg = (await db.execute(
        select(LimitConfig).order_by(LimitConfig.version.desc())
        .limit(1))).scalar_one_or_none()
    return re_.Limits(values=cfg.payload) if cfg else re_.Limits()


async def get_sleeve_state(db: AsyncSession) -> SleeveState:
    """The single lifecycle row — created lazily."""
    st = (await db.execute(
        select(SleeveState).limit(1))).scalar_one_or_none()
    if st is None:
        st = SleeveState(state="active")
        db.add(st)
        await db.flush()
    return st


def _leg_fills(rec: PyramidTradeRec) -> list:
    """Per-leg cost basis from params; legacy rows predate it, so
    degrade to a single leg at `entry` — wrong after adds but the
    best available reconstruction (flagged via _leg_fills_fallback)."""
    fills = (rec.params or {}).get("leg_fills")
    if fills:
        return [dict(f) for f in fills]
    return [{"fill": float(rec.entry), "shares": rec.shares,
             "leg": 1, "reconstructed": True}]


def _cost_basis(rec: PyramidTradeRec) -> float:
    """Dollar cost across every recorded leg fill."""
    return sum(f["shares"] * f["fill"] for f in _leg_fills(rec))


def _rebuild(rec: PyramidTradeRec) -> re_.PyramidTrade:
    """Same hydration the advance endpoint uses — params carries the
    fields the dataclass needs but the row doesn't."""
    params = rec.params or {}
    leg = params.get("leg_shares") or max(
        1, int(rec.shares / (1 + rec.additions)))
    return re_.PyramidTrade(
        symbol="?", entry=rec.entry, atr_initial=rec.atr_initial,
        shares=rec.shares, stop=rec.stop, target1=rec.target1,
        state=re_.PyramidState(rec.state), t2_policy=rec.t2_policy,
        additions=rec.additions, leg_shares=leg,
        atr_current=params.get("atr_current"),
        leg_fills=_leg_fills(rec),
        events=list(rec.events or []))


async def _sleeve_ctx(db: AsyncSession, nav: float,
                      limits: re_.Limits) -> dict:
    """Sleeve ledger from the same formula the risk router uses —
    open pyramid records marked to last close."""
    cfg = re_.sleeve_config(limits)
    out = {"enabled": cfg["enabled"], "config": cfg}
    if not cfg["enabled"]:
        return out
    rows = (await db.execute(
        select(PyramidTradeRec, Instrument)
        .join(Instrument, PyramidTradeRec.instrument_id == Instrument.id)
        .where(PyramidTradeRec.state.in_(_MAINTAIN_STATES)))).all()
    gross = pnl = 0.0
    maint_rates, positions = [], []
    for rec, inst in rows:
        px = (await db.execute(
            select(OhlcvBar.close)
            .where(OhlcvBar.instrument_id == inst.id,
                   OhlcvBar.timeframe == "1d")
            .order_by(OhlcvBar.time.desc()).limit(1))).scalar()
        px = float(px) if px else float(rec.entry)
        cs = float(inst.contract_size or 1)
        mv = rec.shares * px * cs
        gross += mv
        # per-leg cost basis — legs 2+ filled at target prices, not
        # the leg-1 entry; booking all shares at entry overstates P&L
        pnl += (mv - _cost_basis(rec) * cs)
        if inst.maintenance_margin_rate is not None:
            maint_rates.append(float(inst.maintenance_margin_rate))
        positions.append({"symbol": inst.symbol, "state": rec.state,
                          "market_value": mv})
    state = re_.sleeve_state(
        nav, gross, cfg, open_positions=len(rows),
        maint_margin=max(maint_rates) if maint_rates else None)
    state["sleeve_open_pnl"] = pnl
    state["sleeve_pnl_pct"] = (pnl / state["sleeve_equity"]
                             if state["sleeve_equity"] else None)
    life = await get_sleeve_state(db)
    out["cooldown"] = life.state == "cooldown"
    out["state"], out["positions"] = state, positions
    return out


async def _liquidate_sleeve(db: AsyncSession, reason: str) -> int:
    """Portfolio stop is absolute — mark every open sleeve position
    closed, drop the lifecycle row into cooldown, alert. Order
    routing is a separate concern: this is the state-machine
    liquidation the doc demands; execution still goes through the
    normal order path."""
    now = utcnow()
    n = 0
    recs = (await db.execute(
        select(PyramidTradeRec, Instrument)
        .join(Instrument, PyramidTradeRec.instrument_id == Instrument.id)
        .where(PyramidTradeRec.state.in_(_MAINTAIN_STATES)))).all()
    for rec, inst in recs:
        px = (await db.execute(
            select(OhlcvBar.close)
            .where(OhlcvBar.instrument_id == inst.id,
                   OhlcvBar.timeframe == "1d")
            .order_by(OhlcvBar.time.desc()).limit(1))).scalar()
        rec.state = "closed"
        rec.events = [*(rec.events or []),
                      f"{now.isoformat()[:10]} PORTFOLIO STOP — "
                      f"liquidated all {rec.shares}sh "
                      f"@{float(px) if px else 'last'} ({reason})"]
        n += 1
    life = await get_sleeve_state(db)
    life.state = "cooldown"
    life.cooldown_at = now
    life.cooldown_reason = reason
    life.liquidated_at = now
    await emit_alert(
        db, severity="critical", source="monitor:portfolio",
        message=(f"SLEEVE PORTFOLIO STOP — {n} position(s) liquidated, "
                 f"sleeve in cooldown ({reason})"),
        dedup_key="sleeve_liquidated",
        action="human reassessment required to release cooldown")
    return n


async def maintain_open_pyramids(db: AsyncSession) -> dict:
    """The Phase-4 loop over every open pyramid position. Returns a
    per-trade action summary — the job's auditable output."""
    # _portfolio_ctx is the canonical NAV/positions/sleeve assembly —
    # the same context check_order sees on the request path
    from app.routers.risk import _portfolio_ctx
    now = utcnow()
    limits = await _active_limits(db)
    pf = await _portfolio_ctx(db)
    sleeve = pf.get("sleeve") or {}
    max_age = PARAMS["max_bar_age_days"]

    recs = (await db.execute(
        select(PyramidTradeRec, Instrument)
        .join(Instrument, PyramidTradeRec.instrument_id == Instrument.id)
        .where(PyramidTradeRec.state.in_(_MAINTAIN_STATES)))).all()

    out = {"processed": 0, "tightened": 0, "added": 0, "stopped": 0,
           "add_blocked": 0, "stale_skipped": 0, "exits": 0,
           "errors": 0, "actions": []}
    for rec, inst in recs:
        out["processed"] += 1
        try:
            bars = (await db.execute(
                select(OhlcvBar)
                .where(OhlcvBar.instrument_id == inst.id,
                       OhlcvBar.timeframe == "1d")
                .order_by(OhlcvBar.time.desc()).limit(60))
            ).scalars().all()
            if not bars:
                rec.events = [*(rec.events or []),
                              f"{now.isoformat()[:10]} maintain: no "
                              "bars — skipped"]
                continue
            last = bars[0]
            last_t = (last.time.replace(tzinfo=timezone.utc)
                      if last.time.tzinfo is None else last.time)
            age = (now - last_t).days
            if age > max_age:
                # doc data dependency — no adjustments on stale prices
                out["stale_skipped"] += 1
                rec.events = [*(rec.events or []),
                              f"{now.isoformat()[:10]} maintain: bar "
                              f"{age}d stale — no adjustment"]
                await emit_alert(
                    db, severity="warning", source="monitor:trading",
                    message=(f"{inst.symbol}: pyramid data {age}d "
                             "stale — maintenance skipped"),
                    dedup_key=f"pyr_stale:{inst.symbol}",
                    instrument_id=inst.id,
                    action="refresh bars before next close")
                continue

            t_bars = [ti.Bar(t=b.time, o=float(b.open), h=float(b.high),
                             l=float(b.low), c=float(b.close),
                             v=float(b.volume or 0))
                      for b in reversed(bars)]
            atr = ti.atr(t_bars, 14) or rec.atr_initial
            close = float(last.close)

            # ── independent exits (doc Step 20) — a position does not
            # stay open on chart strength alone. Two re-tests every
            # close: fundamentals (four_m proxies) and valuation zone
            # (price ≥ sticker → "VALUATION EXIT / DO NOT ADD").
            # Missing data is NOT deterioration — an unresearched name
            # can't be condemned by a gate that couldn't see it. ──
            exit_reason = None
            try:
                gate = await qual.qualification_gate(
                    db, inst, price=close)
                has_fund = any(
                    isinstance(row, dict)
                    and any(row.get(h) is not None
                            for h in ("10y", "5y", "3y", "1y",
                                      "latest"))
                    for row in gate["five_numbers"].values())
                reasons = []
                if has_fund and not gate["four_ms"]["pass"]:
                    # deterioration = pass → fail. Only a position that
                    # was TRADE_ELIGIBLE at entry has a passing
                    # baseline; a never-qualified record gets a review
                    # alert, not an auto-liquidation on a proxy.
                    qualified = ((rec.params or {}).get("eligibility")
                                 == "TRADE_ELIGIBLE")
                    if qualified:
                        reasons.append("fundamental deterioration "
                                       "(four_ms)")
                    else:
                        await emit_alert(
                            db, severity="warning",
                            source="monitor:trading",
                            message=(f"{inst.symbol}: four_ms failing "
                                     "with no passing baseline — "
                                     "fundamental review required"),
                            dedup_key=f"pyr_fund:{inst.symbol}:{rec.id}",
                            instrument_id=inst.id,
                            action="review fundamentals — position "
                                   "predates the qualification gate")
                if (gate["valuation"]["rule1"].get("zone")
                        == "VALUATION_EXIT"):
                    reasons.append("valuation exit (price ≥ sticker)")
                exit_reason = " + ".join(reasons) or None
            except Exception:
                exit_reason = None        # gate failure ≠ exit signal
            if exit_reason:
                rec.state = "closed"
                rec.events = [
                    *(rec.events or []),
                    f"{now.isoformat()[:10]} EXIT — {exit_reason} "
                    f"@ {close:.2f}"]
                out["exits"] += 1
                out["actions"].append(
                    {"symbol": inst.symbol,
                     "event": f"independent exit — {exit_reason}"})
                await emit_alert(
                    db, severity="warning", source="monitor:trading",
                    message=(f"{inst.symbol}: EXIT — {exit_reason} "
                             f"@ {close:.2f}"),
                    dedup_key=f"pyr_exit:{inst.symbol}:{rec.id}",
                    instrument_id=inst.id,
                    action="position closed by non-ATR exit rule")
                continue

            t = _rebuild(rec)
            events_before = len(t.events)
            prior_additions = t.additions

            # earn the right — a target hit only earns a leg if the
            # sleeve + portfolio gates still have room (doc Phase 4.6)
            add_ok = True
            if t.target1 is not None and close >= t.target1:
                cs = float(inst.contract_size or 1)
                leg_risk = abs(close - t.stop) * t.leg_shares * cs
                gate = re_.check_order(
                    {"symbol": inst.symbol, "side": "buy",
                     "sector": None,
                     "notional": t.leg_shares * close * cs,
                     "risk_dollars": leg_risk,
                     "sleeve": bool(sleeve.get("enabled"))},
                    pf, limits=limits)
                db.add(RiskCheck(
                    symbol=inst.symbol, side="buy",
                    notional=t.leg_shares * close * cs,
                    allowed=gate["allowed"],
                    breaches=gate["breaches"],
                    limits_snapshot=dict(limits.values),
                    engine_version=re_.ENGINE_VERSION,
                    checked_by=None))          # system job
                add_ok = gate["allowed"]

            prior_state = t.state
            res = re_.advance(t, close, atr, add_ok=add_ok)

            rec.state = t.state.value
            rec.shares = t.shares
            rec.stop = t.stop
            rec.target1 = t.target1
            rec.additions = t.additions
            rec.events = t.events
            rec.params = {**(rec.params or {}),
                          "leg_shares": t.leg_shares,
                          "leg_fills": t.leg_fills,
                          "atr_current": t.atr_current}
            rec.engine_version = re_.ENGINE_VERSION

            new_events = t.events[events_before:]
            if t.state == re_.PyramidState.STOPPED \
                    and prior_state != re_.PyramidState.STOPPED:
                out["stopped"] += 1
                await emit_alert(
                    db, severity="critical", source="monitor:trading",
                    message=(f"{inst.symbol}: pyramid STOPPED OUT @ "
                             f"{close:.2f} (stop {rec.stop:.2f})"),
                    dedup_key=f"pyr_stop:{inst.symbol}:{rec.id}",
                    instrument_id=inst.id,
                    observed=close, required=rec.stop,
                    action="exit all legs per stop policy")
            elif t.additions > prior_additions:
                out["added"] += 1
            elif any("maintenance: stop" in e for e in new_events):
                out["tightened"] += 1
            if res.get("note") == "addition blocked":
                out["add_blocked"] += 1
            for e in new_events:
                out["actions"].append(
                    {"symbol": inst.symbol, "event": e})
        except Exception as e:
            out["errors"] += 1
            out["actions"].append({"symbol": inst.symbol,
                                   "error": str(e)[:200]})

    # ── Phase-4 tail: sleeve margin + drawdown recompute (post-adds) ──
    sleeve = await _sleeve_ctx(db, pf["nav"], limits)
    out["sleeve"] = sleeve.get("state")
    if sleeve.get("enabled"):
        st = sleeve["state"]
        # margin-call proximity: used > 90% of equity → critical
        if st["margin_utilisation"] and st["margin_utilisation"] > 0.9:
            await emit_alert(
                db, severity="critical", source="monitor:portfolio",
                message=(f"sleeve margin utilisation "
                         f"{st['margin_utilisation']:.0%} — "
                         "margin-call distance shrinking"),
                dedup_key="sleeve_margin_hot",
                observed=st["margin_utilisation"], required=0.9,
                action="reduce gross before broker forces it")
        # ── doc portfolio stop — ABSOLUTE (Sprint-5 semantics):
        # drawdown is measured from the equity HIGH-WATER mark —
        # profits given back still count as losses taken. Two floors,
        # whichever is tighter: the absolute 20%-of-sleeve-equity stop
        # AND 4% of CURRENT gross exposure (at starter deployment the
        # gross floor fires first — doc Phase 0/2.2). A breach
        # liquidates the sleeve and drops it into cooldown; a human
        # release is the only way back. ──
        life = await get_sleeve_state(db)
        eq_mark = st["sleeve_equity"] + (st["sleeve_open_pnl"] or 0)
        life.equity_hwm = max(life.equity_hwm or 0, eq_mark,
                              st["sleeve_equity"])
        dd_usd = max(0.0, life.equity_hwm - eq_mark)
        dd = (dd_usd / st["sleeve_equity"]
              if st["sleeve_equity"] else 0.0)
        life.drawdown_pct = dd
        cfg = sleeve["config"]
        stop_usd = cfg["portfolio_stop_pct"] * st["sleeve_equity"]
        gross_floor = (cfg["gross_stop_pct"] * st["gross"]
                       if st["gross"] > 0 else float("inf"))
        floor = min(stop_usd, gross_floor)
        if dd_usd >= floor and life.state != "cooldown":
            which = ("gross-exposure" if gross_floor < stop_usd
                     else "equity")
            out["liquidated"] = await _liquidate_sleeve(
                db, f"sleeve drawdown ${dd_usd:,.0f} >= {which} "
                    f"stop ${floor:,.0f}")
            out["sleeve"]["cooldown"] = True
        out["sleeve_drawdown_pct"] = dd
        out["sleeve_drawdown_usd"] = dd_usd
        out["sleeve_stop_floor_usd"] = floor
        out["sleeve_cooldown"] = (life.state == "cooldown")

    out["maintain_version"] = MAINTAIN_VERSION
    return out
