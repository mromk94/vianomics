"""Risk Center + pyramid + order gate. Orders MUST pass
check_order — there is no alternate path (execute routes go through
this gate; an AI agent call cannot approve)."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.session import get_db
from app.models.identity import User
from app.models.instruments import Instrument, Sector
from app.models.macro import RegimeRun
from app.models.market import OhlcvBar
from app.models.portfolio import Position, Portfolio
from app.models.risk import PyramidTradeRec, RiskCheck, TradeIdea
from app.security import audit, require
from app.services import quant as q
from app.services import risk_engine as re_

router = APIRouter(prefix="/risk", tags=["risk"])


async def _portfolio_ctx(db: AsyncSession) -> dict:
    """Live portfolio context for the risk gate."""
    pf = (
        await db.execute(select(Portfolio).limit(1))
    ).scalar_one_or_none()
    positions = []
    cash = nav = 0.0
    if pf:
        from app.models.portfolio import LedgerEntry
        cash = float((await db.execute(
            select(func.sum(LedgerEntry.amount))
            .where(LedgerEntry.portfolio_id == pf.id))).scalar() or 0)
        rows = (
            await db.execute(
                select(Position, Instrument, Sector)
                .join(Instrument, Position.instrument_id == Instrument.id)
                .outerjoin(Sector, Instrument.sector_id == Sector.id)
                .where(Position.portfolio_id == pf.id,
                       Position.quantity > 0)
            )
        ).all()
        for pos, inst, sec in rows:
            bar = (
                await db.execute(
                    select(OhlcvBar)
                    .where(OhlcvBar.instrument_id == inst.id,
                           OhlcvBar.timeframe == "1d")
                    .order_by(OhlcvBar.time.desc()).limit(1)
                )
            ).scalar_one_or_none()
            px = float(bar.close) if bar else float(pos.avg_cost or 0)
            # previous close for daily P&L
            prev = None
            if bar:
                prev = (await db.execute(
                    select(OhlcvBar.close).where(
                        OhlcvBar.instrument_id == inst.id,
                        OhlcvBar.timeframe == "1d",
                        OhlcvBar.time < bar.time)
                    .order_by(OhlcvBar.time.desc()).limit(1)
                )).scalar()
            cs = float(inst.contract_size or 1)
            mv = float(pos.quantity) * px * cs
            adv = float(inst.avg_dollar_volume_30d or 0)
            positions.append({
                "symbol": inst.symbol, "sector": sec.name if sec else "?",
                "market_value": mv, "quantity": float(pos.quantity),
                "avg_cost": float(pos.avg_cost or 0),
                "current_price": px,
                "contract_size": cs,
                "margin_rate": float(inst.margin_rate or 0),
                "maint_margin_rate": (float(inst.maintenance_margin_rate)
                                      if inst.maintenance_margin_rate
                                      is not None else None),
                "stop_price": (float(pos.stop_price)
                               if pos.stop_price is not None else None),
                "target_price": (float(pos.target_price)
                                 if pos.target_price is not None
                                 else None),
                "fair_value": (float(pos.fair_value)
                               if pos.fair_value is not None else None),
                "strategy": pos.strategy,
                "direction": "long" if pos.quantity > 0 else "short",
                "unrealized": float(pos.quantity)
                              * (px - float(pos.avg_cost or 0)) * cs,
                "daily_pnl": (float(pos.quantity) * (px - float(prev))
                              * cs if prev else None),
                "source": "ledger",
                "beta": None,
                # days-to-exit at 10% ADV participation (spec Panel 5
                # liquidity risk) — None when no ADV data
                "liquidity_days": (mv / (adv * 0.10)
                                   if adv > 0 else None),
            })

    # ── external accounts: each MT4/Bamboo position is a real
    # book position — the platform analyzes holdings wherever they
    # live, per-name, not as a black-box aggregate ──
    from app.models.portfolio import ExternalAccount
    ext_accounts = (await db.execute(
        select(ExternalAccount).where(ExternalAccount.connected))
    ).scalars().all()
    if ext_accounts:
        insts = (await db.execute(
            select(Instrument))).scalars().all()
        all_inst = {i.symbol: i for i in insts}
        # alias resolution — ticker identifiers let "FB.OQ" → FB →
        # META resolve to the canonical instrument
        from app.models.instruments import InstrumentIdentifier
        id_map = {i.id: i for i in insts}
        for val, iid in (await db.execute(
                select(InstrumentIdentifier.value,
                       InstrumentIdentifier.instrument_id)
                .where(InstrumentIdentifier.scheme == "ticker"))).all():
            if iid in id_map and val.upper() not in all_inst:
                all_inst[val.upper()] = id_map[iid]
        # live tape — MT4 quotes pushed by the EA; fresher than the
        # snapshot's open-price marks. Only trusted when recent.
        from datetime import timedelta
        from app.models.market import MarketQuote
        qcut = utcnow() - timedelta(minutes=30)
        live_quotes = {q.symbol.upper(): q for q in (await db.execute(
            select(MarketQuote).where(
                MarketQuote.ts >= qcut))).scalars().all()}
    for a in ext_accounts:
        eq = float(a.equity or 0)
        if eq <= 0:
            continue
        # merge same-symbol+direction rows into one position
        merged: dict[str, dict] = {}
        for rp in (a.positions or []):
            sym = str(rp.get("symbol", "")).upper()
            base = sym.split(".")[0]           # AMD.OQ → AMD
            qty = float(rp.get("qty") or 0)
            px = float(rp.get("price") or 0)
            side = str(rp.get("type") or "buy").lower()
            if qty <= 0 or px <= 0:
                continue
            key = f"{sym}:{side}"
            m = merged.setdefault(key, {
                "symbol": base, "display": sym, "qty": 0.0,
                "cost": 0.0, "profit": 0.0, "side": side,
                "bid": None, "ask": None})
            m["qty"] += qty
            m["cost"] += qty * px              # VWAP numerator
            m["profit"] += float(rp.get("profit") or 0)
            m["bid"] = rp.get("bid") or m["bid"]
            m["ask"] = rp.get("ask") or m["ask"]
        ext_margin_rate = float(
            (await _active_limits(db)).get("external_margin_rate")
            or 0.20)
        for sym, m in merged.items():
            inst = all_inst.get(m["symbol"])
            sec = "?"
            if inst and inst.sector_id:
                srow = await db.get(Sector, inst.sector_id)
                sec = srow.name if srow else "?"
            cs = float(inst.contract_size or 1) if inst else 1.0
            # external CFD book: instrument margin_rate if known, else
            # the documented external margin config (20% = 5x book)
            mrate = (float(inst.margin_rate)
                     if inst and inst.margin_rate is not None
                     else ext_margin_rate)
            avg = m["cost"] / m["qty"]
            # mark hierarchy: fresh MT4 quote → per-position pushed
            # bid/ask → open price (last resort; flagged)
            quote = live_quotes.get(m["display"]) or \
                live_quotes.get(m["symbol"])
            mark = None
            if m["side"] == "sell":
                mark = float(quote.ask) if quote and quote.ask \
                    else float(m["ask"]) if m["ask"] else None
                mark_src = "mt4" if quote and quote.ask else \
                    "push" if m["ask"] else None
            else:
                mark = float(quote.bid) if quote and quote.bid \
                    else float(m["bid"]) if m["bid"] else None
                mark_src = "mt4" if quote and quote.bid else \
                    "push" if m["bid"] else None
            if not mark or mark <= 0:
                mark, mark_src = avg, "open"
            mv = m["qty"] * mark
            unreal = m["profit"]
            # direction carried through — the pushed `type` tells the
            # book whether this is a long or short CFD leg
            positions.append({
                "symbol": m["symbol"],
                "display_symbol": m["display"],
                "sector": sec if inst else f"External ({a.source})",
                "market_value": mv * cs,
                "quantity": m["qty"],
                "avg_cost": avg,
                "current_price": mark,
                "contract_size": cs,
                "margin_rate": mrate,
                "maint_margin_rate": (
                    float(inst.maintenance_margin_rate)
                    if inst and inst.maintenance_margin_rate is not None
                    else None),
                "stop_price": None,   # broker pushes no stops
                "target_price": None, "fair_value": None,
                "strategy": None,
                "direction": "short" if m["side"] == "sell" else "long",
                "unrealized": unreal,
                "daily_pnl": None,
                "external": True,
                "source": a.source,
                "beta": 1.0,
                "liquidity_days": 0,
                "instrument_matched": inst is not None,
                "mark_source": mark_src,
                "mark_ts": quote.ts.isoformat() if quote else None,
                "spread": (float(quote.ask) - float(quote.bid)
                           if quote and quote.ask and quote.bid
                           else None),
            })

    # Net Liquidating Equity (spec Panel 1):
    #   equity = cash balance + position market values
    # External accounts contribute their own broker-reported equity
    # (balance + unrealised is already inside it — no double count).
    ext_balance = sum(float(a.balance or 0) for a in ext_accounts)
    nav = (cash
           + sum(p["market_value"] for p in positions
                 if not p.get("external"))
           + sum(float(a.equity or 0) for a in ext_accounts)) or 1
    display_cash = cash + ext_balance

    # latest valuation per held instrument → price vs intrinsic
    try:
        from app.models.valuation import ValuationRun
        for p in positions:
            v = (await db.execute(
                select(ValuationRun)
                .join(Instrument,
                      ValuationRun.instrument_id == Instrument.id)
                .where(Instrument.symbol == p["symbol"])
                .order_by(ValuationRun.created_at.desc()).limit(1))
            ).scalars().first()
            if v:
                o = v.outputs or {}
                iv = (o.get("sticker") or o.get("buy")
                      or (o.get("dcf") or {}).get("fair_value"))
                px = p["market_value"] / p["quantity"] if p["quantity"] else 0
                if iv and px:
                    p["price_vs_iv"] = round(px / iv - 1, 4)
                    p["intrinsic_value"] = iv
    except Exception:
        pass

    regime = (
        await db.execute(select(RegimeRun).order_by(RegimeRun.as_of.desc()))
    ).scalars().first()
    unrealized = sum(p.get("unrealized", 0) for p in positions) \
        if positions else None
    daily = sum(p["daily_pnl"] for p in positions
                if p.get("daily_pnl") is not None) \
        if any(p.get("daily_pnl") is not None for p in positions) \
        else None
    # ── margin + open-stop-risk accounting (new-docs) ──
    margin_used = sum(
        p["market_value"] * (p.get("margin_rate") or 0)
        for p in positions)
    open_stop = 0.0
    unstopped = 0.0
    for p in positions:
        cs = p.get("contract_size") or 1
        px = p.get("current_price") or p["avg_cost"]
        if p.get("stop_price") is not None:
            r = abs(px - p["stop_price"]) * p["quantity"] * cs
            p["open_risk"] = r
            open_stop += r
        else:
            # spec Panel 5: no active stop → no computable stop risk;
            # flagged separately, not silently added to the risk sum
            p["open_risk"] = None
            unstopped += p["market_value"]
    pf = {
        "nav": nav, "cash": display_cash, "positions": positions,
        "unrealized_pnl": unrealized, "daily_pnl": daily,
        "gross": (sum(p["market_value"] for p in positions) / nav
                  if nav else 1),
        "margin_used": margin_used,
        "margin_utilisation": margin_used / nav if nav else 0,
        "open_stop_risk": open_stop,
        "unstopped_notional": unstopped,
        "open_risk": open_stop,
        "avg_correlation": None,  # computed in center view
        "max_dd": None,
        "vix": regime.vix if regime else None,
        "fear_greed": regime.fear_greed if regime else None,
        "as_of": utcnow().isoformat(),
    }
    # Layer-IV sleeve ledger rides on the shared context so every
    # check_order call site sees sleeve capacity automatically.
    pf["sleeve"] = await _sleeve_ctx(db, pf, await _active_limits(db))
    return pf


class OrderIn(BaseModel):
    symbol: str
    side: str = "buy"
    notional: float = Field(gt=0)
    qty: float | None = None
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    strategy: str | None = None


@router.post("/check-order")
async def check_order(
    body: OrderIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("trading:execute")),
) -> dict:
    inst = (
        await db.execute(
            select(Instrument).where(Instrument.symbol == body.symbol.upper())
        )
    ).scalar_one_or_none()
    if inst is None:
        raise HTTPException(404, f"{body.symbol} not in security master")
    sec = (await db.execute(
        select(Sector).where(Sector.id == inst.sector_id))
    ).scalar_one_or_none() if inst.sector_id else None
    ctx = await _portfolio_ctx(db)
    limits = await _active_limits(db)
    cs = float(inst.contract_size or 1)
    order = {"symbol": inst.symbol, "side": body.side,
             "sector": sec.name if sec else None,
             "notional": body.notional,
             "strategy": body.strategy}
    # trade-risk inputs → new-docs limits (open risk, margin, R/R)
    entry = body.entry or (body.notional / body.qty if body.qty else None)
    if entry and body.stop:
        direction = "long" if body.side == "buy" else "short"
        order["stop_invalid"] = (
            direction == "long" and body.stop >= entry) or (
            direction == "short" and body.stop <= entry)
        qty = body.qty or (body.notional / entry)
        order["risk_dollars"] = re_.calculate_risk(
            entry, body.stop, qty, cs)
        order["margin"] = re_.calculate_margin(
            body.notional, float(inst.margin_rate or 0))
        if body.target:
            order["rr"] = re_.calculate_rr(
                re_.calculate_reward(entry, body.target, qty, cs),
                order["risk_dollars"])
    result = re_.check_order(order, ctx, limits=limits)
    result["limits_version"] = limits.version
    # persist audit — every check recorded, with the ACTIVE limits
    rec = RiskCheck(
        symbol=inst.symbol, side=body.side, notional=body.notional,
        allowed=result["allowed"], breaches=result["breaches"],
        limits_snapshot=dict(limits.values),
        engine_version=re_.ENGINE_VERSION, checked_by=user.id)
    db.add(rec)
    await audit(db, action="risk.check_order", actor=user,
                entity_type="risk_check", entity_id=rec.id,
                detail={"symbol": inst.symbol, "allowed": result["allowed"]})
    await db.commit()
    result["check_id"] = rec.id
    return result


# ── pre-trade risk sheet (new-docs Trade Risk Sheet) ──

class PreTradeIn(BaseModel):
    symbol: str
    direction: str = "long"
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    target: float | None = None
    fair_value: float | None = None
    qty: float | None = None          # None → auto-size by risk budget
    strategy: str | None = None
    thesis: str | None = None
    thesis_status: str = "REVIEW"     # VALID|INVALID|REVIEW
    spread: float = 0
    commission: float = 0
    financing: float = 0


@router.post("/pretrade")
async def pretrade(
    body: PreTradeIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("trading:execute")),
) -> dict:
    """Trade Risk Sheet — 'can we take this trade, and how large?'
    Computes the full scorecard and auto-sizes by risk budget when
    qty is omitted. PASS/REVIEW/BLOCK from explicit rules only."""
    inst = (
        await db.execute(
            select(Instrument).where(Instrument.symbol == body.symbol.upper())
        )
    ).scalar_one_or_none()
    if inst is None:
        raise HTTPException(404, f"{body.symbol} not in security master")
    if body.direction == "long" and body.stop >= body.entry:
        raise HTTPException(400, "long stop must be below entry")
    if body.direction == "short" and body.stop <= body.entry:
        raise HTTPException(400, "short stop must be above entry")
    ctx = await _portfolio_ctx(db)
    limits = await _active_limits(db)
    cs = float(inst.contract_size or 1)
    mrate = float(inst.margin_rate or 0)
    equity = ctx["nav"] or 1

    # auto-size: equity × max_trade_risk% ÷ risk-per-unit
    if body.qty is None:
        sz = re_.position_size_for_risk(
            equity, float(limits.get("max_trade_risk_pct")),
            body.entry, body.stop, cs)
        qty = sz["qty"]
    else:
        sz = None
        qty = body.qty

    sheet = re_.trade_risk_sheet(
        entry=body.entry, stop=body.stop, qty=qty, equity=equity,
        contract_size=cs, margin_rate=mrate,
        target=body.target, fair_value=body.fair_value,
        spread=body.spread, commission=body.commission,
        financing=body.financing,
        open_risk_before=ctx["open_risk"],
        direction=body.direction, limits=limits)
    decision = re_.trade_risk_decision(sheet, equity, limits=limits)

    # portfolio gate on the proposed notional as well
    gate = re_.check_order(
        {"symbol": inst.symbol, "side": "buy",
         "sector": None, "notional": sheet["initial_notional"],
         "risk_dollars": sheet["risk_dollars"],
         "margin": sheet["initial_margin"], "rr": sheet["rr"],
         "strategy": body.strategy},
        ctx, limits=limits)
    if not gate["allowed"] and decision["decision"] not in (
            "BLOCK", "INPUT"):
        decision = {"decision": "BLOCK",
                    "reason": "portfolio gate breach",
                    "reasons": ["portfolio gate breach"],
                    "detail": "; ".join(
                        f"{b['rule']} ({b['observed']} vs "
                        f"{b['required']})"
                        for b in gate["breaches"] if b["blocking"])}
    if qty <= 0 and decision["decision"] not in ("BLOCK", "INPUT"):
        decision = {"decision": "REVIEW",
                    "reason": "risk budget sizes to zero",
                    "reasons": ["risk budget sizes to zero"],
                    "detail": "equity × max_trade_risk% ÷ risk-per-unit "
                              "rounds to 0 — raise risk budget or "
                              "tighten stop"}

    rec = RiskCheck(
        symbol=inst.symbol, side=body.direction,
        notional=sheet["initial_notional"],
        allowed=decision["decision"] != "BLOCK",
        breaches=[decision] + gate["breaches"],
        limits_snapshot=dict(limits.values),
        engine_version=re_.ENGINE_VERSION, checked_by=user.id)
    db.add(rec)
    await db.flush()
    # spec §4 — every trade idea persists as a formal Trade object,
    # linking research → risk → execution → audit
    trade = TradeIdea(
        instrument_id=inst.id, risk_check_id=rec.id,
        strategy=body.strategy, direction=body.direction,
        entry_price=body.entry, stop_price=body.stop,
        target_price=body.target, fair_value=body.fair_value,
        proposed_quantity=qty, thesis=body.thesis,
        thesis_status=body.thesis_status,
        status=decision["decision"], sheet=sheet,
        engine_version=re_.ENGINE_VERSION, created_by=user.id)
    db.add(trade)
    await db.flush()
    await audit(db, action="risk.pretrade", actor=user,
                entity_type="trade", entity_id=trade.id,
                detail={"symbol": inst.symbol,
                        "decision": decision["decision"]})
    await db.commit()

    return {
        "trade_id": trade.id,
        "symbol": inst.symbol, "direction": body.direction,
        "entry": body.entry, "stop": body.stop,
        "target": body.target, "fair_value": body.fair_value,
        "strategy": body.strategy,
        "contract_size": cs, "margin_rate": mrate,
        "sizing": sz or {"qty": qty, "source": "manual"},
        "sheet": sheet,
        "decision": decision,
        "gate_breaches": gate["breaches"],
        "limits_version": limits.version,
        "engine": re_.ENGINE_VERSION,
    }


# ── pyramid state machine ──

class PyramidIn(BaseModel):
    symbol: str
    entry: float = Field(gt=0)
    atr: float = Field(gt=0)
    equity: float = Field(gt=0)
    cash: float = Field(gt=0)
    risk_pct: float = Field(0.005, gt=0, le=0.05)
    t2_policy: str = "trailing"
    adv_shares: float | None = None
    force: bool = False   # authorized override of the eligibility gate


@router.post("/pyramid", status_code=201)
async def create_pyramid(
    body: PyramidIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("trading:execute")),
) -> dict:
    inst = (
        await db.execute(
            select(Instrument).where(Instrument.symbol == body.symbol.upper())
        )
    ).scalar_one_or_none()
    if inst is None:
        raise HTTPException(404, f"{body.symbol} not found")
    adv = body.adv_shares
    if adv is None and inst.avg_dollar_volume_30d and body.entry:
        adv = float(inst.avg_dollar_volume_30d) / body.entry
    # sleeve mode: size inside the 30% bucket (risk-budget/starter/
    # gross/margin solver) instead of flat risk_pct
    ctx = await _portfolio_ctx(db)
    sleeve = ctx["sleeve"]

    # Cooldown is absolute — the portfolio stop put the sleeve down;
    # only POST /risk/sleeve/release (human reassessment) reopens it.
    # `force` does NOT bypass this.
    if sleeve["enabled"] and sleeve.get("cooldown"):
        await audit(
            db, action="pyramid.create.rejected", actor=user,
            entity_type="instrument", entity_id=inst.id,
            detail={"symbol": inst.symbol,
                    "verdict": "COOLDOWN",
                    "blocking": ["sleeve_cooldown"]})
        await db.commit()
        raise HTTPException(
            409, {"detail": "trading sleeve is in COOLDOWN — release "
                            "via /risk/sleeve/release after "
                            "reassessment",
                  "lifecycle": sleeve.get("lifecycle")})

    # Trade Eligibility gate — the unified quality+valuation+technical+
    # margin+portfolio object. When the sleeve is on, pyramid creation
    # refuses anything below TRADE_ELIGIBLE unless an authorized
    # override is sent (audited with the gate snapshot).
    eligibility = None
    if sleeve["enabled"]:
        from app.services import eligibility as elig
        eligibility = await elig.trade_eligibility(
            db, inst, ctx, entry=body.entry, atr=body.atr)
        if eligibility["verdict"] != "TRADE_ELIGIBLE" and not body.force:
            await audit(
                db, action="pyramid.create.rejected", actor=user,
                entity_type="instrument", entity_id=inst.id,
                detail={"symbol": inst.symbol,
                        "verdict": eligibility["verdict"],
                        "blocking": eligibility["blocking"]})
            await db.commit()
            raise HTTPException(
                409,
                {"detail": f"{inst.symbol} is {eligibility['verdict']} "
                           "— pyramid requires TRADE_ELIGIBLE",
                 "eligibility": eligibility})

    sizing = None
    if sleeve["enabled"]:
        st = sleeve["state"]
        sizing = re_.sleeve_sizing(
            body.entry, body.atr, st, sleeve["config"],
            asset_gross=sum(
                p["market_value"] for p in sleeve["positions"]
                if p["symbol"] == inst.symbol),
            adv_shares=adv)
    t = re_.create_pyramid(
        inst.symbol, body.equity, body.entry, body.atr, body.cash,
        risk_pct=body.risk_pct, t2_policy=body.t2_policy,
        adv_shares=adv, sizing=sizing)
    rec = PyramidTradeRec(
        instrument_id=inst.id, state=t.state.value, entry=t.entry,
        atr_initial=t.atr_initial, shares=t.shares, stop=t.stop,
        target1=t.target1, t2_policy=t.t2_policy or "adaptive",
        engine_version=re_.ENGINE_VERSION, events=t.events,
        params={"risk_pct": body.risk_pct,
                "leg_shares": t.leg_shares,
                "atr_current": t.atr_current,
                "sleeve": sleeve["enabled"],
                "binding": sizing["binding"] if sizing else None,
                "eligibility": (eligibility["verdict"]
                                if eligibility else None),
                "force_override": body.force})
    db.add(rec)
    await audit(
        db, action=("pyramid.create.override" if body.force
                    else "pyramid.create"), actor=user,
        entity_type="pyramid_trade", entity_id=rec.id,
        detail=({"eligibility": eligibility} if body.force
                and eligibility else None))
    await db.commit()
    return {"id": rec.id, "state": rec.state, "shares": rec.shares,
            "stop": rec.stop, "target1": rec.target1,
            "eligibility": eligibility["verdict"] if eligibility else None,
            "events": rec.events}


class AdvanceIn(BaseModel):
    price: float = Field(gt=0)
    current_atr: float | None = None
    fill_price: float | None = None
    add_ok: bool = True


@router.post("/pyramid/{trade_id}/advance")
async def advance_pyramid(
    trade_id: str, body: AdvanceIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("trading:execute")),
) -> dict:
    rec = await db.get(PyramidTradeRec, trade_id)
    if rec is None:
        raise HTTPException(404, "pyramid trade not found")
    params = rec.params or {}
    leg_shares = params.get("leg_shares") or max(
        1, int(rec.shares / max(1, 2 ** rec.additions)))
    t = re_.PyramidTrade(
        symbol="?", entry=rec.entry, atr_initial=rec.atr_initial,
        shares=rec.shares, stop=rec.stop, target1=rec.target1,
        state=re_.PyramidState(rec.state), t2_policy=rec.t2_policy,
        additions=rec.additions, leg_shares=leg_shares,
        atr_current=params.get("atr_current"),
        events=list(rec.events))

    # spec §14/§25/§29 — a pyramid add is a controlled transaction:
    # the extra leg must re-pass the portfolio risk test, never just
    # because price moved in our favour.
    add_ok = body.add_ok
    gate = None
    if (add_ok and t.target1 is not None
            and body.price >= t.target1):
        inst = await db.get(Instrument, rec.instrument_id)
        cs = float(inst.contract_size or 1) if inst else 1.0
        ctx = await _portfolio_ctx(db)
        limits = await _active_limits(db)
        leg_risk = abs(body.price - t.stop) * leg_shares * cs
        gate = re_.check_order(
            {"symbol": inst.symbol if inst else "?",
             "side": "buy", "sector": None,
             "notional": leg_shares * body.price * cs,
             "risk_dollars": leg_risk,
             "sleeve": bool(ctx["sleeve"]["enabled"])},
            ctx, limits=limits)
        add_ok = gate["allowed"]
        if not add_ok:
            t.log("risk re-check FAILED — "
                  + "; ".join(b["rule"] for b in gate["breaches"]
                              if b.get("blocking")))

    result = re_.advance(
        t, body.price, body.current_atr or rec.atr_initial,
        fill_price=body.fill_price, add_ok=add_ok)
    if gate is not None:
        result["add_recheck"] = {"allowed": gate["allowed"],
                                 "breaches": gate["breaches"]}
    rec.state = t.state.value
    rec.shares = t.shares
    rec.stop = t.stop
    rec.target1 = t.target1
    rec.additions = t.additions
    rec.events = t.events
    rec.params = {**params, "leg_shares": t.leg_shares,
                  "atr_current": t.atr_current}
    rec.engine_version = re_.ENGINE_VERSION
    await db.commit()
    return {"state": rec.state, "shares": rec.shares, "stop": rec.stop,
            "events": rec.events, "result": result}


# ── Layer-IV trading sleeve ──

_OPEN_PYRAMID_STATES = (
    "watchlist", "trade_eligible", "initial_position", "target_1",
    "position_addition", "target_2", "trailing_exit", "partial_exit")


async def _sleeve_ctx(db: AsyncSession, ctx: dict,
                      limits: re_.Limits) -> dict:
    """Sleeve ledger: open pyramid trades marked to last close are the
    sleeve's gross exposure. Maintenance margin comes from instrument
    rates when known (broker-ingested), else the configured default —
    the buffer check keys off whichever is higher-impact."""
    cfg = re_.sleeve_config(limits)
    out = {"enabled": cfg["enabled"], "config": cfg}
    if not cfg["enabled"]:
        return out
    rows = (await db.execute(
        select(PyramidTradeRec, Instrument)
        .join(Instrument, PyramidTradeRec.instrument_id == Instrument.id)
        .where(PyramidTradeRec.state.in_(_OPEN_PYRAMID_STATES)))).all()
    gross = 0.0
    maint_rates: list[float] = []
    positions = []
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
        if inst.maintenance_margin_rate is not None:
            maint_rates.append(float(inst.maintenance_margin_rate))
        positions.append({"symbol": inst.symbol, "state": rec.state,
                          "shares": rec.shares, "market_value": mv,
                          "entry": rec.entry, "stop": rec.stop})
    state = re_.sleeve_state(
        ctx["nav"], gross, cfg, open_positions=len(rows),
        maint_margin=max(maint_rates) if maint_rates else None)
    out["state"] = state
    out["positions"] = positions
    # lifecycle — cooldown is absolute until human release
    from app.models.risk import SleeveState
    life = (await db.execute(
        select(SleeveState).limit(1))).scalar_one_or_none()
    out["cooldown"] = bool(life and life.state == "cooldown")
    if life and life.state == "cooldown":
        out["lifecycle"] = {
            "state": life.state,
            "cooldown_at": (life.cooldown_at.isoformat()
                            if life.cooldown_at else None),
            "reason": life.cooldown_reason,
            "liquidated_at": (life.liquidated_at.isoformat()
                              if life.liquidated_at else None)}
    return out


@router.get("/sleeve")
async def sleeve_status(db: AsyncSession = Depends(get_db)) -> dict:
    """Phase-0 sleeve ledger — equity, gross/margin caps, the
    maintenance-margin buffer verdict, and open sleeve positions."""
    return (await _portfolio_ctx(db))["sleeve"]


class ReleaseIn(BaseModel):
    note: str | None = None


@router.post("/sleeve/release")
async def sleeve_release(
    body: ReleaseIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("trading:execute")),
) -> dict:
    """Human reassessment — the ONLY path out of cooldown. The doc is
    explicit: the portfolio stop is absolute, and re-entry requires a
    review, not a toggle. Audited with actor + note."""
    from app.models.risk import SleeveState
    life = (await db.execute(
        select(SleeveState).limit(1))).scalar_one_or_none()
    if life is None or life.state != "cooldown":
        raise HTTPException(409, "sleeve is not in cooldown")
    life.state = "active"
    life.released_at = utcnow()
    life.released_by = user.id
    life.release_note = body.note
    life.drawdown_pct = None
    await audit(
        db, action="sleeve.cooldown.release", actor=user,
        entity_type="sleeve_state", entity_id=life.id,
        detail={"note": body.note,
                "cooldown_reason": life.cooldown_reason})
    await db.commit()
    return {"state": "active",
            "released_at": life.released_at.isoformat(),
            "released_by": user.id, "note": body.note}


@router.get("/eligibility/{symbol}")
async def trade_eligibility_ep(
    symbol: str, db: AsyncSession = Depends(get_db),
) -> dict:
    """The Trade Eligibility Object — quality (Four M's + five
    numbers) × valuation (price ≤ MOS) × technical timing × sleeve
    margin × portfolio capacity, unified into one verdict with named
    blocking reasons. This is what create_pyramid consults."""
    from app.services import eligibility as elig
    inst = (await db.execute(
        select(Instrument)
        .where(Instrument.symbol == symbol.upper()))).scalar_one_or_none()
    if inst is None:
        raise HTTPException(404, f"{symbol} not in security master")
    return await elig.trade_eligibility(
        db, inst, await _portfolio_ctx(db))


# ── ATR output sheet + pyramid preview ──

@router.get("/atr/{symbol}")
async def atr_output(
    symbol: str,
    d: int = Query(14, ge=1, le=200),
    w: int = Query(14, ge=1, le=200),
    m: int = Query(14, ge=1, le=60),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """The docs' 'ATR Output' sheet — SMA_n(TR) in absolute + % terms,
    horizon windows (6d→576d, 12w→156w, 6m→60m), and the pyramid
    stop/target the state machine would place at the last close.
    d/w/m are the editable periods (default 14, the canonical spec)."""
    from app.services import atr as atr_svc
    rep = await atr_svc.atr_report(db, symbol, d=d, w=w, m=m)
    if rep is None:
        raise HTTPException(404, f"{symbol} not in security master")
    return rep


@router.get("/pyramids")
async def list_pyramids(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """All pyramid trades (open + closed), newest first — the Pyramid
    Trades section's data source."""
    rows = (await db.execute(
        select(PyramidTradeRec, Instrument.symbol)
        .join(Instrument, PyramidTradeRec.instrument_id == Instrument.id)
        .order_by(PyramidTradeRec.created_at.desc())
    )).all()
    return [
        {"id": r.id, "symbol": sym, "state": r.state,
         "entry": r.entry, "shares": r.shares, "stop": r.stop,
         "target1": r.target1, "atr_initial": r.atr_initial,
         "atr_current": (r.params or {}).get("atr_current"),
         "additions": r.additions,
         "engine_version": r.engine_version,
         "created_at": r.created_at.isoformat() if r.created_at else None,
         "events": r.events or []}
        for r, sym in rows]


class PyramidPreviewIn(BaseModel):
    symbol: str
    risk_pct: float = Field(0.005, gt=0, le=0.05)
    entry: float | None = Field(None, gt=0)     # default: last close
    equity: float | None = Field(None, gt=0)    # default: live NAV
    cash: float | None = None                   # default: live cash
    atr_override: float | None = Field(None, gt=0)  # edited-period ATR


@router.post("/pyramid/preview")
async def pyramid_preview(
    body: PyramidPreviewIn,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """The docs' Trade Risk Sheet for a pyramid — REAL ATR from stored
    bars (never the old 3% proxy), position sizing by max-risk %,
    leg ladder, margin estimate. Preview only — creates nothing."""
    from app.services import atr as atr_svc

    inst = (await db.execute(
        select(Instrument).where(Instrument.symbol == body.symbol.upper()))
    ).scalar_one_or_none()
    if inst is None:
        raise HTTPException(404, f"{body.symbol} not in security master")
    rep = await atr_svc.atr_report(db, inst.symbol)
    if rep.get("insufficient") or not rep["daily"]["atr_abs"]:
        raise HTTPException(
            422, f"insufficient bar history for ATR "
                 f"({rep.get('bars', 0)} bars)")

    ctx = await _portfolio_ctx(db)
    equity = body.equity or ctx["nav"]
    cash = body.cash if body.cash is not None else ctx["cash"]
    entry = body.entry or rep["close"]
    if equity <= 0:
        raise HTTPException(
            422, "no portfolio equity — pass `equity` explicitly")
    # UI sends the displayed ATR when the user edits the SMA period —
    # keeps the risk sheet consistent with the shown ATR Output.
    atr_abs = body.atr_override or rep["daily"]["atr_abs"]
    adv_shares = (float(inst.avg_dollar_volume_30d) / entry
                  if inst.avg_dollar_volume_30d and entry else None)

    # Layer-IV sleeve: when enabled the pyramid sizes inside the 30%
    # sleeve — min(risk-budget, starter, gross/margin/liquidity caps)
    # instead of the flat risk_pct-of-equity sizing.
    sleeve = ctx["sleeve"]
    if sleeve["enabled"]:
        st = sleeve["state"]
        asset_gross = sum(
            p["market_value"] for p in sleeve["positions"]
            if p["symbol"] == inst.symbol)
        sz = re_.sleeve_sizing(
            entry, atr_abs, st, sleeve["config"],
            asset_gross=asset_gross, adv_shares=adv_shares)
    else:
        sz = re_.initial_sizing(
            equity, body.risk_pct, entry, atr_abs, cash,
            adv_shares=adv_shares)

    # leg ladder — each target hit adds a standard leg at 3×ATR steps
    legs = [{"leg": 1, "fill": entry, "shares": sz["shares"],
             "cumulative": sz["shares"]}]
    px = entry
    for n in range(2, 5):
        px = px + 3 * atr_abs
        legs.append({"leg": n, "fill": px, "shares": sz["shares"],
                     "cumulative": sz["shares"] * n})

    cs = float(inst.contract_size or 1)
    margin_rate = float(inst.margin_rate or 0)
    eligibility = None
    if sleeve["enabled"]:
        try:
            from app.services import eligibility as elig
            eligibility = await elig.trade_eligibility(
                db, inst, ctx, entry=entry, atr=atr_abs)
        except Exception:
            eligibility = None
    return {
        "symbol": inst.symbol,
        "as_of": rep["as_of"],
        "atr": {"abs": atr_abs,
                "pct": (atr_abs / entry if entry else None)
                if body.atr_override else rep["daily"]["atr_pct"],
                "weekly_pct": rep["weekly"]["atr_pct"]},
        "inputs": {"entry": entry, "equity": equity, "cash": cash,
                   "risk_pct": body.risk_pct},
        "sheet": {
            "stop": sz["stop_price"],
            "stop_distance": sz["stop_distance"],
            "target": entry + 3 * atr_abs,
            "risk_per_share": entry - sz["stop_price"],
            "dollar_risk": sz["dollar_risk"],
            "shares": sz["shares"],
            "shares_raw": sz["shares_raw"],
            "binding": sz["binding"],
            "notional": sz["notional"] * cs,
            "margin_required": (sz.get("margin_required")
                                or sz["notional"] * cs * margin_rate),
            "rr": 3.0 / 1.5,
            "open_risk_pct": (sz["dollar_risk"] / equity
                              if equity else None),
        },
        "sleeve": sleeve,
        "eligibility": eligibility,
        "legs": legs,
        "vol_regime": rep["pyramid"]["vol_regime"],
        "windows": rep["daily"]["windows"],
        "note": "preview only — no trade created; 'Start pyramid' "
                "persists a STATE-2 pyramid with these levels",
    }


# ── Risk Center ──

@router.get("/center")
async def risk_center(db: AsyncSession = Depends(get_db)) -> dict:
    ctx = await _portfolio_ctx(db)
    limits = await _active_limits(db)
    dims = re_.risk_dimensions(ctx, limits=limits)

    # avg pairwise correlation across positions
    syms = [p["symbol"] for p in ctx["positions"]]
    if len(syms) >= 2:
        from app.services.quant_service import correlation_matrix
        cm = await correlation_matrix(db, syms)
        vals = [v for i, row in enumerate(cm["matrix"])
                for j, v in enumerate(row) if i != j and v is not None]
        ctx["avg_correlation"] = sum(vals) / len(vals) if vals else None
        dims = re_.risk_dimensions(ctx, limits=limits)

    # active blocks: re-run gate on hypothetical orders per limit
    blocks = []
    probe = re_.check_order(
        {"symbol": "__probe__", "side": "buy", "sector": None,
         "notional": 1.0}, ctx)
    # portfolio-level checks only (skip order-dependent ones)
    for b in probe["breaches"]:
        if b["rule"] in ("max_drawdown", "vix_reduce", "fg_restriction",
                         "min_cash", "correlation",
                         "portfolio_open_risk", "margin_utilisation"):
            b = dict(b)
            b["timestamp"] = utcnow().isoformat()
            blocks.append(b)

    open_recs = (await db.execute(
        select(PyramidTradeRec, Instrument.symbol)
        .join(Instrument,
              PyramidTradeRec.instrument_id == Instrument.id)
        .where(PyramidTradeRec.state.not_in(
            ["closed", "stopped_out", "rejected"])))
    ).all()
    open_trades = len(open_recs)

    # Part 13 — per-position holding monitor (investment book)
    nav_for_w = ctx["nav"] or 1
    monitors = []
    for p in ctx["positions"]:
        t = re_.holding_tests({
            "weight": p["market_value"] / nav_for_w,
            "price_vs_iv": p.get("price_vs_iv", -1),
            "thesis_broken": False,
            "fundamentals_deteriorated": False,
            "max_weight": 0.10})
        # only surface actionable tests
        actionable = {k: v for k, v in t.items()
                      if v not in ("pass", "no_action")
                      and k != "note"}
        monitors.append({"symbol": p["symbol"],
                         "weight": p["market_value"] / nav_for_w,
                         "price_vs_iv": p.get("price_vs_iv"),
                         "tests": t, "actionable": actionable})

    # external accounts — equity-history risk stats (VaR95, MDD, daily)
    from app.models.portfolio import ExternalAccount
    ext_rows = (await db.execute(
        select(ExternalAccount).where(ExternalAccount.connected))
    ).scalars().all()
    external = []
    for a in ext_rows:
        stats = re_.equity_stats(a.equity_history or [],
                                 float(a.equity) if a.equity else None)
        mdd, var95, daily = (stats["mdd_pct"], stats["var_95"],
                             stats["daily_pnl"])
        external.append({
            "label": a.label, "source": a.source,
            "equity": float(a.equity or 0),
            "balance": float(a.balance or 0),
            "unrealized": (float(a.equity or 0)
                           - float(a.balance or 0)),
            "positions": a.positions or [],
            "mdd_pct": mdd, "var_95": var95, "daily_pnl": daily,
            "synced_at": a.synced_at.isoformat() if a.synced_at
                         else None,
            "stale": bool(a.synced_at and (
                utcnow() - a.synced_at).total_seconds() > 900)})

    # ── new-docs portfolio dashboard block ──
    dash_positions = [{
        "notional": p["market_value"],
        "margin": p["market_value"] * (p.get("margin_rate") or 0),
        "maintenance_margin": (
            p["market_value"] * p["maint_margin_rate"]
            if p.get("maint_margin_rate") is not None else 0),
        "open_risk": p.get("open_risk"),
        "net_pnl": p.get("unrealized") or 0,
        "remaining_reward": (abs(p["target_price"]
                                - (p.get("current_price")
                                   or p["avg_cost"]))
                             * p["quantity"]
                             * (p.get("contract_size") or 1)
                             if p.get("target_price") else 0),
        "direction": p.get("direction") or "long",
        "strategy": p.get("strategy"),
        "sector": p.get("sector"),
    } for p in ctx["positions"]]
    dashboard = re_.portfolio_dashboard(
        dash_positions, ctx["nav"], ctx["cash"], limits=limits)
    dd = None
    for a in external:
        if a.get("mdd_pct") is not None:
            dd = min(dd if dd is not None else 0, a["mdd_pct"])
    escalation = re_.drawdown_escalation(dd or 0, limits=limits)
    stress = re_.named_stress(ctx["positions"], ctx["nav"],
                              limits=limits)
    # maintenance-margin buffer (Exposure doc)
    margin_cfg = None
    if ctx["positions"] and any(p.get("margin_rate")
                                for p in ctx["positions"]):
        blended_mr = max(
            float(p.get("margin_rate") or 0)
            for p in ctx["positions"])
        # broker-specific maintenance margin if ingested, else the
        # engine conservatively assumes maintenance == initial
        mm = next((p["maint_margin_rate"]
                   for p in ctx["positions"]
                   if p.get("maint_margin_rate") is not None), None)
        margin_cfg = re_.margin_call_check(
            ctx["nav"], ctx["gross"] * ctx["nav"],
            margin_requirement=blended_mr,
            maintenance_margin=mm,
            portfolio_stop_pct=float(
                limits.get("max_drawdown_pct") or 0.20))

    return {
        "external": external,
        "monitors": monitors,
        "pyramid_trades": [
            {"id": r.id, "symbol": sym, "state": r.state,
             "entry": r.entry, "shares": r.shares, "stop": r.stop,
             "target1": r.target1,
             "atr_initial": r.atr_initial,
             "atr_current": (r.params or {}).get("atr_current"),
             "signal_engine": (r.params or {}).get("signal_engine"),
             "created_at": (r.created_at.isoformat()
                            if r.created_at else None),
             "events": (r.events or [])[-3:],
             "additions": r.additions} for r, sym in open_recs],
        "nav": ctx["nav"], "cash": ctx["cash"],
        "positions": ctx["positions"],
        "dashboard": dashboard,
        "margin": {
            "used": ctx["margin_used"],
            "utilisation": ctx["margin_utilisation"],
            "call_buffer_check": margin_cfg,
        },
        "drawdown": {"max_dd": dd, "escalation": escalation},
        "named_stress": stress,
        "dimensions": dims,
        "limits": dict(limits.values),
        "active_blocks": blocks,
        "open_pyramid_trades": open_trades,
        "vix": ctx["vix"], "fear_greed": ctx["fear_greed"],
        "engine": re_.ENGINE_VERSION,
        "as_of": ctx["as_of"],
    }


@router.get("/portfolio")
async def risk_portfolio(db: AsyncSession = Depends(get_db)) -> dict:
    """Spec §28 — GET /risk/portfolio: the aggregate dashboard view
    (notional/leverage/margin/open-risk/capacity/stress/DD)."""
    ctx = await _portfolio_ctx(db)
    limits = await _active_limits(db)
    dash_positions = [{
        "notional": p["market_value"],
        "margin": p["market_value"] * (p.get("margin_rate") or 0),
        "maintenance_margin": (
            p["market_value"] * p["maint_margin_rate"]
            if p.get("maint_margin_rate") is not None else 0),
        "open_risk": p.get("open_risk"),
        "net_pnl": p.get("unrealized") or 0,
        "direction": p.get("direction") or "long",
        "strategy": p.get("strategy"),
        "sector": p.get("sector"),
    } for p in ctx["positions"]]
    dash = re_.portfolio_dashboard(
        dash_positions, ctx["nav"], ctx["cash"], limits=limits)
    return {
        "dashboard": dash,
        "named_stress": re_.named_stress(
            ctx["positions"], ctx["nav"], limits=limits),
        "limits_version": getattr(limits, "version", 0),
        "engine": re_.ENGINE_VERSION,
        "as_of": ctx["as_of"],
    }


@router.get("/trades")
async def list_trades(limit: int = 50,
                      db: AsyncSession = Depends(get_db)) -> list:
    """Formal trade objects created by /risk/pretrade — the
    research→risk→execution audit link (spec §4/§28)."""
    rows = (await db.execute(
        select(TradeIdea, Instrument.symbol)
        .join(Instrument, TradeIdea.instrument_id == Instrument.id)
        .order_by(TradeIdea.created_at.desc()).limit(limit))).all()
    return [{"id": t.id, "symbol": sym, "direction": t.direction,
             "strategy": t.strategy, "entry": t.entry_price,
             "stop": t.stop_price, "target": t.target_price,
             "fair_value": t.fair_value, "qty": t.proposed_quantity,
             "status": t.status, "thesis_status": t.thesis_status,
             "risk_check_id": t.risk_check_id,
             "engine": t.engine_version,
             "at": t.created_at.isoformat() if t.created_at else None}
            for t, sym in rows]


@router.get("/trades/{trade_id}")
async def get_trade(trade_id: str,
                    db: AsyncSession = Depends(get_db)) -> dict:
    t = await db.get(TradeIdea, trade_id)
    if t is None:
        raise HTTPException(404, "trade not found")
    return {"id": t.id, "direction": t.direction,
            "strategy": t.strategy, "entry": t.entry_price,
            "stop": t.stop_price, "target": t.target_price,
            "fair_value": t.fair_value, "qty": t.proposed_quantity,
            "status": t.status, "thesis": t.thesis,
            "thesis_status": t.thesis_status, "sheet": t.sheet,
            "risk_check_id": t.risk_check_id,
            "engine": t.engine_version,
            "at": t.created_at.isoformat() if t.created_at else None}


@router.get("/checks")
async def checks(limit: int = 50, db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(RiskCheck).order_by(RiskCheck.checked_at.desc())
            .limit(limit))
    ).scalars().all()
    return [
        {"id": r.id, "symbol": r.symbol, "side": r.side,
         "notional": r.notional, "allowed": r.allowed,
         "breaches": r.breaches,
         "checked_at": r.checked_at.isoformat() if r.checked_at else None}
        for r in rows
    ]


# ── editable limit config (versioned, auditable) ──

async def _active_limits(db: AsyncSession) -> re_.Limits:
    from app.models.risk import LimitConfig
    r = (await db.execute(
        select(LimitConfig).order_by(LimitConfig.version.desc())
        .limit(1))).scalars().first()
    lim = re_.Limits(values=r.payload) if r else re_.Limits()
    lim.version = r.version if r else 0
    return lim


@router.get("/limits")
async def get_limits(db: AsyncSession = Depends(get_db)) -> dict:
    from app.models.risk import LimitConfig
    r = (await db.execute(
        select(LimitConfig).order_by(LimitConfig.version.desc())
        .limit(1))).scalars().first()
    return {
        "version": r.version if r else 0,
        "limits": r.payload if r else dict(re_.DEFAULT_LIMITS),
        "source": "configured" if r else "defaults",
        "history": [] if r is None else [
            {"version": x.version, "note": x.note,
             "at": x.created_at.isoformat()}
            for x in (await db.execute(
                select(LimitConfig)
                .order_by(LimitConfig.version.desc()).limit(10))
            ).scalars().all()],
    }


class LimitsIn(BaseModel):
    note: str | None = None
    max_drawdown_pct: float | None = None
    max_sector_pct: float | None = None
    max_single_name_pct: float | None = None
    min_sectors: int | None = None
    max_correlation: float | None = None
    min_cash_pct: float | None = None
    risk_per_trade_pct: float | None = None
    max_gross_leverage: float | None = None
    vix_reduce_above: float | None = None
    fg_block_new_above: float | None = None
    vol_reduction_vol: float | None = None
    # new-docs risk hierarchy
    max_trade_risk_pct: float | None = None
    max_portfolio_open_risk_pct: float | None = None
    max_strategy_risk_pct: float | None = None
    max_margin_utilisation_pct: float | None = None
    warn_drawdown_pct: float | None = None
    reduce_drawdown_pct: float | None = None
    halt_drawdown_pct: float | None = None
    min_rr: float | None = None
    max_positions: int | None = None
    starter_fraction: float | None = None
    external_margin_rate: float | None = None
    # Layer-IV trading sleeve (V2 doc Phase 0)
    sleeve_enabled: bool | None = None
    sleeve_pct: float | None = None
    sleeve_target_leverage: float | None = None
    sleeve_initial_margin: float | None = None
    sleeve_maint_margin: float | None = None
    sleeve_max_positions: int | None = None
    sleeve_max_asset_gross_pct: float | None = None
    sleeve_starter_fraction: float | None = None
    sleeve_portfolio_stop_pct: float | None = None


@router.put("/limits", status_code=201)
async def put_limits(
    body: LimitsIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("admin:*")),
) -> dict:
    """New immutable version — the gate reads the latest always."""
    from app.models.risk import LimitConfig
    cur = await _active_limits(db)
    payload = dict(cur.values)
    updates = body.model_dump(exclude={"note"}, exclude_none=True)
    payload.update(updates)
    # sanity bounds — reject obviously broken configs
    for k in ("max_drawdown_pct", "max_sector_pct",
              "max_single_name_pct", "min_cash_pct"):
        v = payload.get(k)
        if v is not None and not (0 < float(v) <= 1):
            raise HTTPException(400, f"{k} must be within (0, 1]")
    last = (await db.execute(
        select(func.max(LimitConfig.version)))).scalar() or 0
    rec = LimitConfig(version=last + 1, payload=payload,
                      created_by=user.id, note=body.note)
    db.add(rec)
    await audit(db, action="risk.limits_update", actor=user,
                entity_type="limit_config", entity_id=rec.id,
                detail=updates)
    await db.commit()
    return {"version": rec.version, "limits": payload}
