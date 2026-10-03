"""PM Decision Engine — deterministic ENTER/ADD/HOLD/REDUCE/EXIT/
BLOCK/REVIEW states over the real book (spec §20–23, workbook
PM Decision). Hard risk rules veto first; every decision is
persisted to pm_decisions for the audit trail."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.governance import DecisionRecord
from app.models.identity import User
from app.models.instruments import Instrument
from app.models.market import TechnicalScanResult
from app.models.risk import PmDecision
from app.routers.risk import _active_limits, _portfolio_ctx
from app.security import audit, require
from app.services import risk_engine as re_

router = APIRouter(prefix="/pm", tags=["pm"])


class EvalIn(BaseModel):
    symbol: str
    thesis: str = "REVIEW"            # VALID|INVALID|REVIEW
    valuation: str = "REVIEW"         # ATTRACTIVE|REACHED|REVIEW
    technical: str = "REVIEW"         # CONFIRMED|EXIT|WEAKENING|REVIEW
    risk_check: str = "REVIEW"        # PASS|REVIEW|BLOCK
    portfolio_check: str = "REVIEW"   # PASS|REVIEW|BLOCK
    has_position: bool = False
    risk_capacity_ok: bool = True
    concentration_high: bool = False
    target_hit: bool = False
    stop_breached: bool = False
    rr: float | None = None


@router.post("/evaluate", status_code=201)
async def evaluate(
    body: EvalIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("trading:execute")),
) -> dict:
    """Explicit-checks PM evaluation — the rule matrix from the
    workbook. Persisted with book context (audit §27)."""
    ctx = await _portfolio_ctx(db)
    out = re_.pm_decide(
        thesis=body.thesis, valuation=body.valuation,
        technical=body.technical, risk_check=body.risk_check,
        portfolio_check=body.portfolio_check,
        has_position=body.has_position,
        risk_capacity_ok=body.risk_capacity_ok,
        concentration_high=body.concentration_high,
        target_hit=body.target_hit, stop_breached=body.stop_breached)
    rec = PmDecision(
        symbol=body.symbol.upper(), decision=out["decision"],
        reason=out["reason"],
        inputs=body.model_dump(exclude={"symbol"}),
        equity=ctx["nav"],
        exposure=sum(p["market_value"] for p in ctx["positions"]),
        open_risk=ctx["open_risk"], leverage=ctx["gross"],
        rr=body.rr, engine_version=re_.ENGINE_VERSION,
        decided_by=user.id)
    db.add(rec)
    await audit(db, action="pm.evaluate", actor=user,
                entity_type="pm_decision", entity_id=rec.id,
                detail={"symbol": rec.symbol,
                        "decision": rec.decision})
    await db.commit()
    return {"id": rec.id, "decision": rec.decision,
            "reason": rec.reason,
            "context": {"equity": rec.equity,
                        "exposure": rec.exposure,
                        "open_risk": rec.open_risk,
                        "leverage": rec.leverage}}


def _verdict_thesis(v: str | None) -> str:
    if v in ("approve", "approved", "conditional"):
        return "VALID"
    if v in ("reject", "block", "rejected"):
        return "INVALID"
    return "REVIEW"


def _tech_check(decision: str | None) -> str:
    return {"entry_signal": "CONFIRMED", "invalid": "EXIT",
            "wait": "REVIEW", "no_trade": "REVIEW"}.get(
                decision or "", "REVIEW")


@router.get("/positions")
async def pm_positions(db: AsyncSession = Depends(get_db)) -> dict:
    """Live per-position PM state — thesis/valuation/technical/risk/
    portfolio checks computed from stored engine outputs, then the
    decision matrix. Every row gets a concrete PM state."""
    ctx = await _portfolio_ctx(db)
    limits = await _active_limits(db)
    nav = ctx["nav"] or 1

    inst_map = {i.symbol: i for i in (await db.execute(
        select(Instrument))).scalars().all()}
    scan_map = {}
    dec_map = {}
    for p in ctx["positions"]:
        inst = inst_map.get(p["symbol"])
        if not inst:
            continue
        ts = (await db.execute(
            select(TechnicalScanResult)
            .where(TechnicalScanResult.instrument_id == inst.id)
            .order_by(TechnicalScanResult.created_at.desc()).limit(1))
        ).scalars().first()
        scan_map[p["symbol"]] = ts.decision if ts else None
        dr = (await db.execute(
            select(DecisionRecord)
            .where(DecisionRecord.instrument_id == inst.id)
            .order_by(DecisionRecord.at.desc()).limit(1))
        ).scalars().first()
        dec_map[p["symbol"]] = dr.verdict if dr else None

    # portfolio-level check: dashboard status + probe gate
    dash_pos = [{"notional": p["market_value"],
                 "open_risk": (abs((p.get("current_price")
                                   or p["avg_cost"])
                                  - p["stop_price"]) * p["quantity"]
                               * (p.get("contract_size") or 1)
                               if p.get("stop_price") is not None
                               else None),
                 "direction": p.get("direction") or "long",
                 "strategy": p.get("strategy")}
                for p in ctx["positions"]]
    dash = re_.portfolio_dashboard(dash_pos, nav, ctx["cash"],
                                   limits=limits)
    capacity_ok = dash["risk_capacity"] > 0
    portfolio_check = ("BLOCK" if dash["status"] == "REDUCE"
                       and dash["margin_utilisation"] > float(
                           limits.get("max_margin_utilisation_pct") or 1)
                       else "REVIEW" if dash["status"] == "REDUCE"
                       else "PASS")

    rows = []
    for p in ctx["positions"]:
        thesis = _verdict_thesis(dec_map.get(p["symbol"]))
        pvi = p.get("price_vs_iv")
        valuation = ("REACHED" if pvi is not None and pvi >= 0
                     else "ATTRACTIVE" if pvi is not None
                     else "REVIEW")
        technical = _tech_check(scan_map.get(p["symbol"]))
        open_r = (abs((p.get("current_price") or p["avg_cost"])
                      - p["stop_price"]) * p["quantity"]
                  * (p.get("contract_size") or 1)
                  if p.get("stop_price") is not None else
                  p["market_value"])
        risk_check = ("PASS" if open_r / nav <= float(
                        limits.get("max_trade_risk_pct") or 0.02)
                      else "REVIEW")
        concentration = p["market_value"] / nav > float(
            limits.get("max_single_name_pct") or 1)
        target_hit = (p.get("target_price") is not None
                      and (p.get("current_price") or p["avg_cost"])
                      >= p["target_price"])
        stop_hit = (p.get("stop_price") is not None
                    and (p.get("current_price") or p["avg_cost"])
                    <= p["stop_price"])
        out = re_.pm_decide(
            thesis=thesis, valuation=valuation, technical=technical,
            risk_check=risk_check, portfolio_check=portfolio_check,
            has_position=True, risk_capacity_ok=capacity_ok,
            concentration_high=concentration, target_hit=target_hit,
            stop_breached=stop_hit)
        rows.append({
            "symbol": p["symbol"],
            "display_symbol": p.get("display_symbol"),
            "source": p.get("source"),
            "weight": p["market_value"] / nav,
            "checks": {"thesis": thesis, "valuation": valuation,
                       "technical": technical,
                       "risk_check": risk_check,
                       "portfolio_check": portfolio_check},
            "pm_decision": out["decision"], "reason": out["reason"],
            "open_risk": open_r, "unstopped": p.get("stop_price") is None,
        })
    return {"positions": rows,
            "portfolio": {"status": dash["status"],
                          "pm_action": dash["pm_action"],
                          "risk_capacity": dash["risk_capacity"],
                          "open_risk": dash["open_risk"],
                          "margin_utilisation":
                          dash["margin_utilisation"]},
            "engine": re_.ENGINE_VERSION}


@router.get("/decisions")
async def pm_decisions(limit: int = 50,
                       db: AsyncSession = Depends(get_db)) -> list:
    rows = (await db.execute(
        select(PmDecision).order_by(PmDecision.created_at.desc())
        .limit(limit))).scalars().all()
    return [{"id": r.id, "symbol": r.symbol, "decision": r.decision,
             "reason": r.reason, "inputs": r.inputs,
             "equity": r.equity, "open_risk": r.open_risk,
             "leverage": r.leverage, "rr": r.rr,
             "at": r.created_at.isoformat() if r.created_at else None}
            for r in rows]
