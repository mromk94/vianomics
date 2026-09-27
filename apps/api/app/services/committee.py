"""Committee orchestrator — Parts 16–21.

Pipeline: gather engine ctx → run 9 bounded agents → strict schema
validation → conflict resolution (RM missing = NO_TRADE, BLOCK =
NO_TRADE) → CIO synthesis → DecisionRecord + AgentRuns/Outputs +
Approval(pending). Human approval is always required; no AI can
approve or route an order.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import analyzers
from app.agents.registry import AGENTS
from app.agents.report import validate_report, AgentReport
from app.db.base import utcnow
from app.models.agents import AgentOutput, AgentRun
from app.models.governance import Approval, DecisionRecord
from app.models.instruments import Instrument, Sector
from app.services import mandate as mandate_svc
from app.services import quant_service as qs
from app.services import risk_engine as re_
from app.services import technical_engine as te
from app.services import valuation_service as vs
from app.services.macro_regime import classify as macro_classify
from app.models.market import OhlcvBar
from app.models.screening import ScreeningResult
from app.models.universe import Universe, UniverseMembership

AGENT_ORDER = ["fundamental", "valuation", "rule_one", "quant",
               "macro", "technical", "risk", "pm"]

WEIGHTS = {"fundamental": 0.20, "valuation": 0.20, "rule_one": 0.10,
           "quant": 0.10, "macro": 0.10, "technical": 0.10,
           "risk": 0.0, "pm": 0.20}


async def _latest_price(db, inst_id) -> float | None:
    b = (await db.execute(
        select(OhlcvBar).where(OhlcvBar.instrument_id == inst_id,
                               OhlcvBar.timeframe == "1d")
        .order_by(OhlcvBar.time.desc()).limit(1))).scalar_one_or_none()
    return float(b.close) if b else None


async def gather_ctx(db: AsyncSession, inst: Instrument,
                     growth: float = 0.15) -> dict:
    """All engine outputs — agents never query providers directly."""
    as_of = utcnow()
    price = await _latest_price(db, inst.id)
    mandate = await mandate_svc.get_active(db)
    sec = (await db.execute(select(Sector).where(
        Sector.id == inst.sector_id))).scalar_one_or_none() \
        if inst.sector_id else None

    valuation = None
    if price:
        run = await vs.run_valuation(
            db, inst, price=price, growth=growth, mandate=mandate)
        valuation = run.outputs

    risk_ctx = {"nav": 1_000_000, "cash": 200_000, "positions": [],
                "vix": None, "fear_greed": None}
    regime = await macro_classify(db, as_of)
    risk_ctx["vix"], risk_ctx["fear_greed"] = regime["vix"], regime["fear_greed"]

    # RM gate on a hypothetical 2%-of-nav allocation
    gate = re_.check_order(
        {"symbol": inst.symbol, "side": "buy",
         "sector": sec.name if sec else None,
         "notional": 20_000}, risk_ctx)

    # green zone latest score for this instrument
    gz = (
        await db.execute(
            select(ScreeningResult)
            .where(ScreeningResult.instrument_id == inst.id)
            .order_by(ScreeningResult.created_at.desc())
            .limit(1))
    ).scalar_one_or_none()
    green_zone = (
        {"score": float(gz.score) if gz.score else None,
         "status": gz.verdict, "run_id": gz.run_id} if gz else None)

    # active universe membership → gate 1
    in_univ = bool((
        await db.execute(
            select(UniverseMembership)
            .join(Universe, UniverseMembership.universe_id == Universe.id)
            .where(UniverseMembership.instrument_id == inst.id,
                   UniverseMembership.status == "active",
                   Universe.tier == "approved")
            .limit(1))
    ).scalar_one_or_none())

    return {
        "symbol": inst.symbol, "sector": sec.name if sec else None,
        "price": price, "as_of": as_of,
        "mandate_version": mandate.version if mandate else None,
        "valuation_outputs": valuation,
        "quant": await qs.instrument_metrics(db, inst, as_of),
        "macro": regime,
        "technical": await te.evaluate(db, inst, as_of),
        "risk_gate": gate,
        "green_zone": green_zone,
        "in_universe": in_univ,
    }


ANALYZER_FN = {
    "fundamental": analyzers.fundamental,
    "valuation": analyzers.valuation,
    "rule_one": analyzers.rule_one,
    "quant": analyzers.quant,
    "macro": analyzers.macro,
    "technical": analyzers.technical,
    "risk": analyzers.risk_manager,
    "pm": analyzers.portfolio_manager,
}


def resolve_conflict(reports: dict[str, AgentReport | None]) -> dict:
    """Formal conflict protocol — deterministic, not averaged."""
    rm = reports.get("risk")
    if rm is None:
        return {"verdict": "no_trade",
                "reason": "Risk Manager response missing — never a pass",
                "disagreements": ["risk agent produced no output"]}
    if rm.recommendation == "block":
        return {"verdict": "no_trade",
                "reason": "Risk Manager BLOCK — absolute veto",
                "disagreements": []}

    # disagreements: any split across agent recommendations
    recs = {k: r.recommendation for k, r in reports.items()
            if r and k not in ("risk", "cio")}
    disagreements = []
    if len(set(recs.values())) > 1:
        disagreements.append(
            "agent split: " + ", ".join(f"{k}={v}" for k, v in recs.items()))
    contradictions = [c for r in reports.values() if r
                      for c in r.contradictions]
    return {"verdict": None, "disagreements": disagreements + contradictions}


def cio_synthesize(reports: dict[str, AgentReport | None],
                   ctx: dict, conflict: dict) -> dict:
    """Deterministic CIO synthesis — weighted score + explicit
    confidence model (NOT a probability of positive returns)."""
    scores = {k: r.score for k, r in reports.items()
              if r and k in WEIGHTS}
    tw = sum(WEIGHTS[k] for k in scores)
    score = sum(scores[k] * WEIGHTS[k] for k in scores) / tw if tw else 0

    agree = sum(1 for r in reports.values()
                if r and r.recommendation in
                ("buy", "strong_buy", "pass")) / max(1, sum(
                    1 for r in reports.values() if r))
    gaps = sum(len(r.data_gaps) for r in reports.values() if r)
    contradictions = len(conflict.get("disagreements", []))
    data_q = sum(1 for r in reports.values()
                 if r and r.data_quality == "high") / 8

    conf = max(0.0, min(1.0,
        0.35 + 0.25 * agree + 0.20 * data_q
        - 0.05 * contradictions - 0.03 * gaps))

    v = ctx.get("valuation_outputs") or {}
    mos = (v.get("mos") or {}).get("discount")
    iv = (v.get("dcf") or {}).get("per_share")
    price = ctx.get("price")

    sizing = None
    if price and (v.get("dcf") or {}).get("per_share"):
        anchors = (ctx.get("valuation_outputs", {})
                   .get("sector_valuation", {}))
        sizing = {"suggested_risk_pct": 0.005,
                  "note": "pyramid sizing via risk engine"}

    verdict = conflict["verdict"] or (
        "approve_pending_human" if score >= 70 else
        "watchlist" if score >= 50 else "reject")

    return {
        "verdict": verdict,
        "score": round(score, 1),
        "confidence": round(conf, 2),
        "confidence_note": ("confidence = agreement + evidence quality "
                            "− contradictions/gaps; NOT a probability "
                            "of positive returns"),
        "recommendation_horizon": "12–24m",
        "expected_return": mos,
        "downside_scenarios": (v.get("sensitivity", {}) or {}).get("grid"),
        "margin_of_safety": mos,
        "intrinsic_value": iv,
        "reward_to_risk": None,
        "invalidation_conditions": [c for r in reports.values() if r
                                    for c in r.fail_criteria][:6],
        "position_sizing": sizing,
        "portfolio_fit": {"sector": ctx.get("sector"),
                          "gate_allowed": ctx["risk_gate"]["allowed"]},
        "required_human_approval": True,
        "report": {
            k: {"recommendation": r.recommendation, "score": r.score,
                "confidence": r.confidence, "conclusion": r.conclusion}
            for k, r in reports.items() if r},
        "disagreements": conflict.get("disagreements", []),
        "veto_reason": conflict.get("reason"),
    }


async def run_committee(
    db: AsyncSession, inst: Instrument, *, growth: float = 0.15,
    actor_id: str | None = None,
) -> dict:
    ctx = await gather_ctx(db, inst, growth)

    reports: dict[str, AgentReport | None] = {}
    raw: dict[str, dict] = {}
    for key in AGENT_ORDER:
        run = AgentRun(agent_key=key, instrument_id=inst.id,
                       model=AGENTS[key]["prompt_version"],
                       prompt_hash=AGENTS[key]["prompt_version"],
                       status="running", inputs={"symbol": inst.symbol})
        db.add(run)
        await db.flush()
        try:
            rep = validate_report(ANALYZER_FN[key](ctx))
            reports[key] = rep
            run.status = "complete"
            db.add(AgentOutput(
                run_id=run.id, recommendation=rep.recommendation,
                score=rep.score, confidence=rep.confidence,
                data_quality=rep.data_quality,
                payload=rep.model_dump(mode="json")))
            raw[key] = rep.model_dump(mode="json")
        except Exception as e:  # missing agent → conflict rule handles
            run.status = "failed"
            run.error = str(e)[:500]
            reports[key] = None

    conflict = resolve_conflict(reports)
    cio = cio_synthesize(reports, ctx, conflict)

    # Part 22 — decision tree over the same ctx + CIO verdict
    from app.services.decision_tree import evaluate_tree, entry_protocol
    ctx["cio_verdict"] = cio["verdict"]
    tree = evaluate_tree(ctx)
    entry = entry_protocol(ctx)

    # CIO run record
    cio_run = AgentRun(agent_key="cio", instrument_id=inst.id,
                       model=AGENTS["cio"]["prompt_version"],
                       prompt_hash=AGENTS["cio"]["prompt_version"],
                       status="complete")
    db.add(cio_run)
    await db.flush()
    db.add(AgentOutput(
        run_id=cio_run.id, recommendation=cio["verdict"],
        score=cio["score"], confidence=cio["confidence"],
        data_quality="high",
        payload={"cio": cio}))

    # DecisionRecord + pending human approval
    dr = DecisionRecord(
        instrument_id=inst.id, at=utcnow(), stage="researched",
        verdict=cio["verdict"],
        gate_results={"conflict": conflict,
                      "tree": tree,
                      "entry_protocol": entry,
                      "risk_gate": ctx["risk_gate"]["breaches"]},
        agent_scores={k: r.score for k, r in reports.items() if r},
        numbers={**{
            "price": ctx["price"],
            "iv": cio["intrinsic_value"],
            "mos": cio["margin_of_safety"],
            # Part 33 completeness — execution-relevant numbers
            "atr_14": (ctx.get("technical", {}).get("indicators", {})
                       or {}).get("atr_14"),
            "sticker": ((ctx.get("valuation_outputs") or {})
                        .get("rule1") or {}).get("sticker_price"),
            "buy_price": ((ctx.get("valuation_outputs") or {})
                          .get("rule1") or {}).get("buy_price"),
            "risk_gate_allowed": ctx["risk_gate"]["allowed"],
            "pm_score": reports.get("pm") and reports["pm"].score,
            "rm_verdict": (reports.get("risk") and
                           reports["risk"].recommendation),
        }},
        cio_confidence=cio["confidence"],
        mandate_version=ctx["mandate_version"],
    )
    db.add(dr)
    await db.flush()
    db.add(Approval(decision_id=dr.id, requested_by="cio",
                    status="pending",
                    reason="committee synthesis — human review required"))
    await db.flush()

    return {
        "decision_id": dr.id,
        "verdict": cio["verdict"],
        "symbol": inst.symbol,
        "price": ctx["price"],
        "cio": cio,
        "tree": tree,
        "entry_protocol": entry,
        "agents": raw,
        "missing_agents": [k for k, r in reports.items() if r is None],
        "engine_refs": {
            "committee": "committee/v1.0",
            "technical": te.PARAMS_VERSION,
            "risk": re_.ENGINE_VERSION,
            "valuation": "rule1-dcf/v1.0",
        },
    }
