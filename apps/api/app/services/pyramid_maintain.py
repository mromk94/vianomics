"""Per-close pyramid maintenance — the doc's Phase-4 checklist, run
as one job:

    CURRENT ATR
        ↓
    1.5 × ATR  →  tighten-only ratchet (never widen)
        ↓
    target hit → earn-the-right risk re-check → add leg
        ↓
    close trigger (stop breach / independent exit / portfolio
    stop) → EXIT PROPOSED → human approval → closed
        ↓
    sleeve margin + drawdown recomputed → alerts

CLOSING IS NEVER AUTOMATIC: every close trigger queues an
`exit_request` on the record and raises a critical alert — the
position stays open until a human approves via
POST /risk/pyramid/{id}/exit. The portfolio stop still drops the
sleeve into cooldown immediately (no NEW risk), but liquidating
the open legs waits for the human too.

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
from app.models.instruments import Instrument, Sector
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


def _rec_direction(rec: PyramidTradeRec) -> str:
    return getattr(rec, "direction", None) or "long"


def _rebuild(rec: PyramidTradeRec) -> re_.PyramidTrade:
    """Same hydration the advance endpoint uses — params carries the
    fields the dataclass needs but the row doesn't."""
    params = rec.params or {}
    leg = params.get("leg_shares") or max(
        1, int(rec.shares / (1 + rec.additions)))
    return re_.PyramidTrade(
        symbol="?", entry=rec.entry, atr_initial=rec.atr_initial,
        shares=rec.shares, stop=rec.stop, target1=rec.target1,
        direction=_rec_direction(rec),
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
    sector_gross: dict[str, float] = {}
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
        # the leg-1 entry; booking all shares at entry overstates P&L.
        # Short P&L is reversed: profit when price falls.
        basis = _cost_basis(rec) * cs
        pnl += (basis - mv) if _rec_direction(rec) == "short" \
            else (mv - basis)
        sec = None
        if inst.sector_id:
            s = await db.get(Sector, inst.sector_id)
            sec = s.name if s else None
        sector_gross[sec or "?"] = sector_gross.get(sec or "?", 0) + mv
        if inst.maintenance_margin_rate is not None:
            maint_rates.append(float(inst.maintenance_margin_rate))
        positions.append({"symbol": inst.symbol, "state": rec.state,
                          "direction": _rec_direction(rec),
                          "sector": sec, "market_value": mv})
    state = re_.sleeve_state(
        nav, gross, cfg, open_positions=len(rows),
        maint_margin=max(maint_rates) if maint_rates else None)
    state["sleeve_open_pnl"] = pnl
    state["sleeve_pnl_pct"] = (pnl / state["sleeve_equity"]
                             if state["sleeve_equity"] else None)
    # Step-18 metric — sector concentration inside the sleeve
    top_sec, top_g = (max(sector_gross.items(), key=lambda kv: kv[1])
                      if sector_gross else (None, 0.0))
    state["sector_concentration"] = {
        "by_sector_gross": {k: round(v, 2) for k, v in
                            sector_gross.items()},
        "top_sector": top_sec,
        "top_sector_share_of_gross": (top_g / gross if gross else None),
        "cap_pct": cfg["max_sector_pct"],
        "cap_usd": state["effective_gross_cap"] * cfg["max_sector_pct"]}
    life = await get_sleeve_state(db)
    out["cooldown"] = life.state == "cooldown"
    out["state"], out["positions"] = state, positions
    return out


async def mark_instrument_exited(db: AsyncSession, inst: Instrument,
                                 now) -> None:
    """Instrument status on position close — EXITED only when no
    other pyramid record on the same name is still open."""
    still_open = (await db.execute(
        select(PyramidTradeRec.id).where(
            PyramidTradeRec.instrument_id == inst.id,
            PyramidTradeRec.state.in_(_MAINTAIN_STATES)).limit(1))
    ).scalar()
    if not still_open:
        inst.status, inst.status_at = "exited", now


async def _propose_exit(db: AsyncSession, rec: PyramidTradeRec,
                        inst: Instrument, *, reason: str, trigger: str,
                        price: float, proposed_state: str,
                        now) -> bool:
    """Queue a close for human approval — the loop NEVER closes a
    position on its own. Returns True when a new request was queued
    (False when one is already pending)."""
    if (rec.params or {}).get("exit_request"):
        return False
    rec.params = {
        **(rec.params or {}),
        "exit_request": {"reason": reason, "trigger": trigger,
                         "price": price,
                         "proposed_at": now.isoformat(),
                         "proposed_state": proposed_state}}
    rec.events = [*(rec.events or []),
                  f"{now.isoformat()[:10]} EXIT PROPOSED — {reason} "
                  f"@ {price:.2f} — awaiting human approval"]
    await emit_alert(
        db, severity="critical", source="monitor:trading",
        message=(f"{inst.symbol}: EXIT APPROVAL REQUIRED — {reason} "
                 f"@ {price:.2f}"),
        dedup_key=f"pyr_exit_req:{inst.symbol}:{rec.id}",
        instrument_id=inst.id, observed=price, required=rec.stop,
        action=(f"approve or reject via "
                f"POST /risk/pyramid/{rec.id}/exit"))
    return True


async def _queue_liquidation(db: AsyncSession, reason: str) -> int:
    """Portfolio stop is absolute about RISK, not about execution —
    it drops the sleeve into cooldown immediately (no new exposure)
    and queues every open position for human-approved liquidation.
    Closes never happen without the human."""
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
        await _propose_exit(
            db, rec, inst, reason=f"portfolio stop — {reason}",
            trigger="portfolio_stop",
            price=float(px) if px else float(rec.entry),
            proposed_state="closed", now=now)
        # liquidated names go to instrument COOLDOWN — the doc's
        # post-risk-event universe state; release is sleeve-level
        inst.status, inst.status_at = "cooldown", now
        n += 1
    life = await get_sleeve_state(db)
    life.state = "cooldown"
    life.cooldown_at = now
    life.cooldown_reason = reason
    life.liquidated_at = now
    await emit_alert(
        db, severity="critical", source="monitor:portfolio",
        message=(f"SLEEVE PORTFOLIO STOP — {n} position(s) queued for "
                 f"liquidation, sleeve in cooldown ({reason})"),
        dedup_key="sleeve_liquidated",
        action="approve each pending exit — closes require a human")
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
    # doc: data must be "current at every close" — the freshness
    # window is an operator config (default 4 calendar days, covering
    # holiday weekends), not the looser display-level PARAMS value
    max_age = (sleeve.get("config") or {}).get(
        "data_max_age_days") or PARAMS["max_bar_age_days"]

    recs = (await db.execute(
        select(PyramidTradeRec, Instrument)
        .join(Instrument, PyramidTradeRec.instrument_id == Instrument.id)
        .where(PyramidTradeRec.state.in_(_MAINTAIN_STATES)))).all()

    out = {"processed": 0, "tightened": 0, "added": 0, "stopped": 0,
           "add_blocked": 0, "stale_skipped": 0,
           "exits_proposed": 0, "exit_pending": 0,
           "unchanged_period": 0, "errors": 0, "actions": []}
    # doc Phase 0 — the strategy's ATR timeframe is a config, not a
    # constant: 1d (default), 1w or 1mo. Wider frames need a deep
    # enough daily pull to resample (monthly ATR-14 wants ~15 closed
    # months ≈ 330 sessions).
    tf = (sleeve.get("config") or {}).get("atr_timeframe", "1d")
    bar_limit = 60 if tf == "1d" else 450
    for rec, inst in recs:
        out["processed"] += 1
        try:
            # a queued close waits on a human — the loop touches
            # nothing on a name whose exit is pending approval
            if (rec.params or {}).get("exit_request"):
                out["exit_pending"] += 1
                continue
            bars = (await db.execute(
                select(OhlcvBar)
                .where(OhlcvBar.instrument_id == inst.id,
                       OhlcvBar.timeframe == "1d")
                .order_by(OhlcvBar.time.desc()).limit(bar_limit))
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

            t_bars_all = [ti.Bar(t=b.time, o=float(b.open),
                                 h=float(b.high), l=float(b.low),
                                 c=float(b.close),
                                 v=float(b.volume or 0))
                          for b in reversed(bars)]
            # doc Phase 3: the state machine runs "at every timeframe
            # close" — for 1w/1mo configs that means only COMPLETED
            # periods; the still-open period is dropped and a sweep in
            # an already-processed period is a no-op.
            tf_period_key = None
            if tf in ("1w", "1mo"):
                from app.services.atr import _resample
                groups = _resample(
                    [{"time": b.t, "open": b.o, "high": b.h,
                      "low": b.l, "close": b.c} for b in t_bars_all],
                    tf)
                now_key = (now.isocalendar()[:2] if tf == "1w"
                           else (now.year, now.month))

                def _key(g):
                    t = g["time"]
                    return (t.isocalendar()[:2] if tf == "1w"
                            else (t.year, t.month))

                closed_groups = [g for g in groups
                                 if _key(g) != now_key]
                if not closed_groups:
                    rec.events = [*(rec.events or []),
                                  f"{now.isoformat()[:10]} maintain: "
                                  f"no closed {tf} period yet"]
                    continue
                tf_period_key = list(_key(closed_groups[-1]))
                if (rec.params or {}).get("last_tf_period") \
                        == tf_period_key:
                    out["unchanged_period"] += 1
                    continue
                t_bars = [ti.Bar(t=g["time"], o=g["open"], h=g["high"],
                                 l=g["low"], c=g["close"], v=0.0)
                          for g in closed_groups]
                atr = ((ti.atr_workbook(t_bars, tf) or {})
                       .get("atr_abs")) or rec.atr_initial
                close = float(closed_groups[-1]["close"])
            else:
                t_bars = t_bars_all
                atr = ((ti.atr_workbook(t_bars, "1d") or {})
                       .get("atr_abs")) or rec.atr_initial
                close = float(last.close)

            # ── independent exits (doc Step 20) — a position does not
            # stay open on chart strength alone. Two re-tests every
            # close: fundamentals (four_m proxies) and valuation zone
            # (price ≥ sticker → "VALUATION EXIT / DO NOT ADD").
            # Direction reversed for shorts: a short COVERS at/below
            # intrinsic value (price ≤ sticker — profit realised), and
            # the four-ms deterioration exit does not apply — a short's
            # thesis IS deterioration; the exit would be improvement.
            # Missing data is NOT deterioration — an unresearched name
            # can't be condemned by a gate that couldn't see it. ──
            direction = _rec_direction(rec)
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
                if direction != "short" \
                        and has_fund and not gate["four_ms"]["pass"]:
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
                sticker = (gate["valuation"]["rule1"]
                           .get("sticker_price"))
                if direction == "short":
                    if sticker and close <= sticker:
                        reasons.append("valuation cover "
                                       "(price ≤ sticker)")
                elif (gate["valuation"]["rule1"].get("zone")
                        == "VALUATION_EXIT"):
                    reasons.append("valuation exit (price ≥ sticker)")
                exit_reason = " + ".join(reasons) or None
            except Exception:
                exit_reason = None        # gate failure ≠ exit signal
            if exit_reason:
                # propose, never close — a human approves via
                # POST /risk/pyramid/{id}/exit
                if await _propose_exit(
                        db, rec, inst, reason=exit_reason,
                        trigger="independent", price=close,
                        proposed_state="closed", now=now):
                    out["exits_proposed"] += 1
                    out["actions"].append(
                        {"symbol": inst.symbol,
                         "event": f"exit proposed — {exit_reason}"})
                continue

            t = _rebuild(rec)
            events_before = len(t.events)
            prior_additions = t.additions

            # stop breach is a close trigger — it queues an exit
            # request like every other close path; advance() only
            # runs when no close is pending on this bar. Direction
            # reversed for shorts: the stop sits ABOVE price.
            stop_hit = (close >= t.stop if direction == "short"
                        else close <= t.stop)
            if t.stop is not None and stop_hit:
                cmp = "≥" if direction == "short" else "≤"
                if await _propose_exit(
                        db, rec, inst,
                        reason=(f"stop breached — close {close:.2f} "
                                f"{cmp} stop {t.stop:.2f}"),
                        trigger="stop_breach", price=close,
                        proposed_state="stopped_out", now=now):
                    out["exits_proposed"] += 1
                    out["actions"].append(
                        {"symbol": inst.symbol,
                         "event": f"exit proposed — stop breach @ "
                                  f"{close:.2f}"})
                continue

            # earn the right — a target hit only earns a leg if the
            # sleeve + portfolio gates still have room (doc Phase 4.6).
            # For shorts the target sits BELOW price and the add is a
            # sell-side order through the same capacity gates.
            target_hit = (t.target1 is not None and
                          (close <= t.target1 if direction == "short"
                           else close >= t.target1))
            add_ok = True
            if target_hit:
                cs = float(inst.contract_size or 1)
                leg_risk = abs(close - t.stop) * t.leg_shares * cs
                side = "sell" if direction == "short" else "buy"
                sec_name = None
                if inst.sector_id:
                    s = await db.get(Sector, inst.sector_id)
                    sec_name = s.name if s else None
                gate = re_.check_order(
                    {"symbol": inst.symbol, "side": side,
                     "sector": sec_name,
                     "notional": t.leg_shares * close * cs,
                     "risk_dollars": leg_risk,
                     "sleeve": bool(sleeve.get("enabled"))},
                    pf, limits=limits)
                db.add(RiskCheck(
                    symbol=inst.symbol, side=side,
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
                          "atr_current": t.atr_current,
                          **({"last_tf_period": tf_period_key}
                             if tf_period_key else {})}
            rec.engine_version = re_.ENGINE_VERSION

            new_events = t.events[events_before:]
            if t.state == re_.PyramidState.STOPPED \
                    and prior_state != re_.PyramidState.STOPPED:
                # unreachable while the pre-check above holds — but a
                # close must never slip through unapproved: roll the
                # state back and queue the request anyway
                t.state = prior_state
                rec.state = prior_state.value
                if await _propose_exit(
                        db, rec, inst,
                        reason=(f"stop breached — close {close:.2f} "
                                f"≤ stop {t.stop:.2f}"),
                        trigger="stop_breach", price=close,
                        proposed_state="stopped_out", now=now):
                    out["exits_proposed"] += 1
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
        # margin-call proximity: used > 90% of equity → the doc's
        # "reduce exposure before the broker calls". Reductions are
        # PROPOSED, not executed: queue exits on the largest positions
        # (biggest gross first) until projected gross sits back under
        # the broker-call bound — a human approves each close via
        # POST /risk/pyramid/{id}/exit.
        if st["margin_utilisation"] and st["margin_utilisation"] > 0.9:
            await emit_alert(
                db, severity="critical", source="monitor:portfolio",
                message=(f"sleeve margin utilisation "
                         f"{st['margin_utilisation']:.0%} — "
                         "margin-call distance shrinking"),
                dedup_key="sleeve_margin_hot",
                observed=st["margin_utilisation"], required=0.9,
                action="reduce gross before broker forces it")
            target = st.get("margin_call_at_gross")
            if target:
                # hold a 10% buffer inside the broker bound
                target *= 0.9
                proj_gross = st["gross"]
                queued = 0
                by_symbol = {inst.symbol: (rec, inst)
                             for rec, inst in recs}
                for pos in sorted(sleeve["positions"],
                                  key=lambda p: p["market_value"],
                                  reverse=True):
                    if proj_gross <= target:
                        break
                    pair = by_symbol.get(pos["symbol"])
                    if pair is None:
                        continue
                    rec, inst = pair
                    px = (pos["market_value"] / rec.shares
                          if rec.shares else rec.entry)
                    # positions already pending count toward the cut
                    if await _propose_exit(
                            db, rec, inst,
                            reason=("margin-call proximity — sleeve "
                                    "gross must come down before the "
                                    "broker forces it"),
                            trigger="margin_call", price=px,
                            proposed_state="closed", now=now):
                        queued += 1
                    proj_gross -= pos["market_value"]
                if queued:
                    out["margin_reduction_queued"] = queued
        # ── doc portfolio stop — ABSOLUTE about risk: a breach drops
        # the sleeve into cooldown immediately (no new exposure) and
        # queues every open position for liquidation. Closes still
        # require human approval — cooldown removes the ability to
        # ADD risk while the human decides. Two floors, whichever is
        # tighter: 20% of sleeve equity AND 4% of current gross
        # exposure (doc Phase 0/2.2), drawdown measured off the
        # equity high-water mark. ──
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
            out["liquidation_queued"] = await _queue_liquidation(
                db, f"sleeve drawdown ${dd_usd:,.0f} >= {which} "
                    f"stop ${floor:,.0f}")
            out["sleeve"]["cooldown"] = True
        out["sleeve_drawdown_pct"] = dd
        out["sleeve_drawdown_usd"] = dd_usd
        out["sleeve_stop_floor_usd"] = floor
        out["sleeve_stop_distance_usd"] = floor - dd_usd
        out["sleeve_cooldown"] = (life.state == "cooldown")

    out["maintain_version"] = MAINTAIN_VERSION
    return out
