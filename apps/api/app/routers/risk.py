"""Risk Center + pyramid + order gate. Orders MUST pass
check_order — there is no alternate path (execute routes go through
this gate; an AI agent call cannot approve)."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
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
from app.models.risk import PyramidTradeRec, RiskCheck
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
                "beta": None, "liquidity_days": None,
            })
        nav = sum(p["market_value"] for p in positions) or 1

    # ── external accounts: each MT4/Bamboo position is a real
    # book position — the platform analyzes holdings wherever they
    # live, per-name, not as a black-box aggregate ──
    from app.models.portfolio import ExternalAccount
    ext_accounts = (await db.execute(
        select(ExternalAccount).where(ExternalAccount.connected))
    ).scalars().all()
    if ext_accounts:
        all_inst = {i.symbol: i for i in (await db.execute(
            select(Instrument))).scalars().all()}
    for a in ext_accounts:
        eq = float(a.equity or 0)
        if eq <= 0:
            continue
        # merge same-symbol rows into one position per account+symbol
        merged: dict[str, dict] = {}
        for rp in (a.positions or []):
            sym = str(rp.get("symbol", "")).upper()
            base = sym.split(".")[0]           # AMD.OQ → AMD
            qty = float(rp.get("qty") or 0)
            px = float(rp.get("price") or 0)
            if qty <= 0 or px <= 0:
                continue
            m = merged.setdefault(sym, {
                "symbol": base, "display": sym, "qty": 0.0,
                "cost": 0.0, "profit": 0.0})
            m["qty"] += qty
            m["cost"] += qty * px              # VWAP numerator
            m["profit"] += float(rp.get("profit") or 0)
        ext_margin_rate = float(
            re_.DEFAULT_LIMITS["external_margin_rate"])
        for sym, m in merged.items():
            mv = m["qty"] * (m["cost"] / m["qty"])
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
            positions.append({
                "symbol": m["symbol"],
                "display_symbol": m["display"],
                "sector": sec if inst else f"External ({a.source})",
                "market_value": mv * cs,
                "quantity": m["qty"],
                "avg_cost": m["cost"] / m["qty"],
                "current_price": m["cost"] / m["qty"],
                "contract_size": cs,
                "margin_rate": mrate,
                "maint_margin_rate": (
                    float(inst.maintenance_margin_rate)
                    if inst and inst.maintenance_margin_rate is not None
                    else None),
                "stop_price": None,   # broker pushes no stops
                "target_price": None, "fair_value": None,
                "strategy": None, "direction": "long",
                "unrealized": m["profit"],
                "daily_pnl": None,
                "external": True,
                "source": a.source,
                "beta": 1.0,
                "liquidity_days": 0,
                "instrument_matched": inst is not None,
            })
        # account equity is capital at risk — nav, not position sum
        cash += float(a.balance or 0)
        equity_total = eq  # noqa: F841 — tracked below via ext_nav

    nav = (sum(p["market_value"] for p in positions
               if not p.get("external"))
           + sum(float(a.equity or 0) for a in ext_accounts)) or 1

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
            open_stop += abs(px - p["stop_price"]) * p["quantity"] * cs
        else:
            # unstopped position → full notional is capital at risk
            unstopped += p["market_value"]
    return {
        "nav": nav, "cash": cash, "positions": positions,
        "unrealized_pnl": unrealized, "daily_pnl": daily,
        "gross": (sum(p["market_value"] for p in positions) / nav
                  if nav else 1),
        "margin_used": margin_used,
        "margin_utilisation": margin_used / nav if nav else 0,
        "open_stop_risk": open_stop,
        "unstopped_notional": unstopped,
        "open_risk": open_stop + unstopped,
        "avg_correlation": None,  # computed in center view
        "max_dd": None,
        "vix": regime.vix if regime else None,
        "fear_greed": regime.fear_greed if regime else None,
        "as_of": utcnow().isoformat(),
    }


class OrderIn(BaseModel):
    symbol: str
    side: str = "buy"
    notional: float = Field(gt=0)
    qty: float | None = None
    entry: float | None = None
    stop: float | None = None
    target: float | None = None


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
             "notional": body.notional}
    # trade-risk inputs → new-docs limits (open risk, margin, R/R)
    entry = body.entry or (body.notional / body.qty if body.qty else None)
    if entry and body.stop:
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
    # persist audit — every check recorded
    rec = RiskCheck(
        symbol=inst.symbol, side=body.side, notional=body.notional,
        allowed=result["allowed"], breaches=result["breaches"],
        limits_snapshot=dict(re_.DEFAULT_LIMITS),
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
        open_risk_before=ctx["open_risk"])
    decision = re_.trade_risk_decision(sheet, equity, limits=limits)

    # portfolio gate on the proposed notional as well
    gate = re_.check_order(
        {"symbol": inst.symbol, "side": "buy",
         "sector": None, "notional": sheet["initial_notional"],
         "risk_dollars": sheet["risk_dollars"],
         "margin": sheet["initial_margin"], "rr": sheet["rr"]},
        ctx, limits=limits)
    if not gate["allowed"] and decision["decision"] != "BLOCK":
        decision = {"decision": "BLOCK",
                    "reason": "portfolio gate breach",
                    "detail": "; ".join(
                        f"{b['rule']} ({b['observed']} vs "
                        f"{b['required']})"
                        for b in gate["breaches"] if b["blocking"])}

    rec = RiskCheck(
        symbol=inst.symbol, side=body.direction,
        notional=sheet["initial_notional"],
        allowed=decision["decision"] != "BLOCK",
        breaches=[decision] + gate["breaches"],
        limits_snapshot=dict(limits.values),
        engine_version=re_.ENGINE_VERSION, checked_by=user.id)
    db.add(rec)
    await audit(db, action="risk.pretrade", actor=user,
                entity_type="risk_check", entity_id=rec.id,
                detail={"symbol": inst.symbol,
                        "decision": decision["decision"]})
    await db.commit()

    return {
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
    t = re_.create_pyramid(
        inst.symbol, body.equity, body.entry, body.atr, body.cash,
        risk_pct=body.risk_pct, t2_policy=body.t2_policy,
        adv_shares=body.adv_shares)
    rec = PyramidTradeRec(
        instrument_id=inst.id, state=t.state.value, entry=t.entry,
        atr_initial=t.atr_initial, shares=t.shares, stop=t.stop,
        target1=t.target1, t2_policy=t.t2_policy or "adaptive",
        engine_version=re_.ENGINE_VERSION, events=t.events,
        params={"risk_pct": body.risk_pct,
                "leg_shares": t.leg_shares,
                "atr_current": t.atr_current})
    db.add(rec)
    await audit(db, action="pyramid.create", actor=user,
                entity_type="pyramid_trade", entity_id=rec.id)
    await db.commit()
    return {"id": rec.id, "state": rec.state, "shares": rec.shares,
            "stop": rec.stop, "target1": rec.target1,
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
    result = re_.advance(
        t, body.price, body.current_atr or rec.atr_initial,
        fill_price=body.fill_price, add_ok=body.add_ok)
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


# ── Risk Center ──

@router.get("/center")
async def risk_center(db: AsyncSession = Depends(get_db)) -> dict:
    ctx = await _portfolio_ctx(db)
    dims = re_.risk_dimensions(ctx)

    # avg pairwise correlation across positions
    syms = [p["symbol"] for p in ctx["positions"]]
    if len(syms) >= 2:
        from app.services.quant_service import correlation_matrix
        cm = await correlation_matrix(db, syms)
        vals = [v for i, row in enumerate(cm["matrix"])
                for j, v in enumerate(row) if i != j and v is not None]
        ctx["avg_correlation"] = sum(vals) / len(vals) if vals else None
        dims = re_.risk_dimensions(ctx)

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
    limits = await _active_limits(db)
    dash_positions = [{
        "notional": p["market_value"],
        "margin": p["market_value"] * (p.get("margin_rate") or 0),
        "open_risk": (abs((p.get("current_price") or p["avg_cost"])
                         - p["stop_price"]) * p["quantity"]
                      * (p.get("contract_size") or 1)
                      if p.get("stop_price") is not None else None),
        "net_pnl": p.get("unrealized") or 0,
        "remaining_reward": (abs(p["target_price"]
                                - (p.get("current_price")
                                   or p["avg_cost"]))
                             * p["quantity"]
                             * (p.get("contract_size") or 1)
                             if p.get("target_price") else 0),
        "direction": p.get("direction") or "long",
        "strategy": p.get("strategy"),
    } for p in ctx["positions"]]
    dashboard = re_.portfolio_dashboard(
        dash_positions, ctx["nav"], ctx["cash"], limits=limits)
    dd = None
    for a in external:
        if a.get("mdd_pct") is not None:
            dd = min(dd if dd is not None else 0, a["mdd_pct"])
    escalation = re_.drawdown_escalation(dd or 0, limits=limits)
    stress = re_.named_stress(ctx["positions"], ctx["nav"])
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
        "limits": limits,
        "active_blocks": blocks,
        "open_pyramid_trades": open_trades,
        "vix": ctx["vix"], "fear_greed": ctx["fear_greed"],
        "engine": re_.ENGINE_VERSION,
        "as_of": ctx["as_of"],
    }


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
