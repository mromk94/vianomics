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
            mv = float(pos.quantity) * px
            positions.append({
                "symbol": inst.symbol, "sector": sec.name if sec else "?",
                "market_value": mv, "quantity": float(pos.quantity),
                "beta": None, "liquidity_days": None,
            })
        nav = sum(p["market_value"] for p in positions) or 1

    regime = (
        await db.execute(select(RegimeRun).order_by(RegimeRun.as_of.desc()))
    ).scalars().first()
    return {
        "nav": nav, "cash": cash, "positions": positions,
        "gross": nav / nav if nav else 1,
        "margin_used": 0,
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
    result = re_.check_order(
        {"symbol": inst.symbol, "side": body.side,
         "sector": sec.name if sec else None, "notional": body.notional},
        ctx)
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
        target1=t.target1, t2_policy=t.t2_policy,
        engine_version=re_.ENGINE_VERSION, events=t.events,
        params={"risk_pct": body.risk_pct})
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
    t = re_.PyramidTrade(
        symbol="?", entry=rec.entry, atr_initial=rec.atr_initial,
        shares=rec.shares, stop=rec.stop, target1=rec.target1,
        state=re_.PyramidState(rec.state), t2_policy=rec.t2_policy,
        additions=rec.additions, events=list(rec.events))
    result = re_.advance(
        t, body.price, body.current_atr or rec.atr_initial,
        fill_price=body.fill_price, add_ok=body.add_ok)
    rec.state = t.state.value
    rec.shares = t.shares
    rec.stop = t.stop
    rec.additions = t.additions
    rec.events = t.events
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
                         "min_cash", "correlation"):
            b = dict(b)
            b["timestamp"] = utcnow().isoformat()
            blocks.append(b)

    open_trades = (
        await db.execute(
            select(func.count(PyramidTradeRec.id)).where(
                PyramidTradeRec.state.not_in(
                    ["closed", "stopped_out", "rejected"]))
        )
    ).scalar()

    return {
        "nav": ctx["nav"], "cash": ctx["cash"],
        "positions": ctx["positions"],
        "dimensions": dims,
        "limits": re_.DEFAULT_LIMITS,
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
