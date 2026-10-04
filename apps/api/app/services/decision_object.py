"""The doc's Step-25 Decision Object — one standardized, machine-
readable verdict per instrument that every downstream consumer
(UI, CIO, audit, execution) reads instead of re-deriving state.

Composes, in the doc's own chain order:

    mandate → screening/green-zone → research(four M's, five numbers)
    → valuation (status + MOS zone) → technical timing
    → risk gate (check_order breaches) → sleeve ledger
    → qualification verdict → eligibility verdict
    → CIO advisory (latest committee record — NEVER a gate override)
    → human approval requirement

The object is the audit spine: every field cites its engine version,
every rejection names its blocking reasons, and `approval_required`
is ALWAYS true — AI conviction can annotate a decision, never
authorize one.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.models.governance import DecisionRecord
from app.models.instruments import Instrument
from app.models.market import OhlcvBar
from app.services import eligibility as elig
from app.services import qualification as qual
from app.services import risk_engine as re_

OBJECT_VERSION = "decision/v1.0"

# eligibility verdict → the doc's action vocabulary. Nothing here
# routes an order — ENTER means "eligible to propose", not "bought".
_DECISION = {
    "TRADE_ELIGIBLE": "ENTER",
    "COOLDOWN": "BLOCK",
    "BLOCKED": "BLOCK",
    "WATCHLIST": "WATCH",
    "WATCH": "REVIEW",
    "REJECTED": "REJECT",
}


async def decision_object(
    db: AsyncSession, inst: Instrument, pf_ctx: dict | None = None,
) -> dict:
    """Emit the standardized object. `pf_ctx` optional — pulled when
    not supplied (endpoints already have it hot)."""
    if pf_ctx is None:
        from app.routers.risk import _portfolio_ctx
        pf_ctx = await _portfolio_ctx(db)

    gate = await qual.gate_with_price(db, inst)
    e = await elig.trade_eligibility(db, inst, pf_ctx)
    sleeve = pf_ctx.get("sleeve") or {}

    # latest committee record — advisory only
    dr = (await db.execute(
        select(DecisionRecord)
        .where(DecisionRecord.instrument_id == inst.id)
        .order_by(DecisionRecord.at.desc()).limit(1))
    ).scalars().first()

    px = (await db.execute(
        select(OhlcvBar.close)
        .where(OhlcvBar.instrument_id == inst.id,
               OhlcvBar.timeframe == "1d")
        .order_by(OhlcvBar.time.desc()).limit(1))).scalar()
    price = float(px) if px else e.get("price")

    decision = _DECISION.get(e["verdict"], "REVIEW")
    risk_flags = list(e["blocking"])
    if sleeve.get("cooldown"):
        risk_flags.append("sleeve_lifecycle_cooldown")
    if (sleeve.get("state") or {}).get("buffer_capped"):
        risk_flags.append("margin_buffer_capped")
    tech = e["gates"].get("technical") or {}
    if tech.get("data_fresh") is False:
        risk_flags.append("stale_price_data")

    # confidence: deterministic, explainable — gate strength, not a
    # return forecast. Full pass = 1.0; each soft miss −0.2, hard
    # miss −0.4; floor 0.
    soft = sum(1 for g in ("technical", "margin", "portfolio")
               if not (e["gates"].get(g) or {}).get("pass", True))
    hard = sum(1 for g in ("quality", "valuation")
               if not (e["gates"].get(g) or {}).get("pass", True))
    confidence = round(max(0.0, 1.0 - 0.2 * soft - 0.4 * hard), 2)

    return {
        "version": OBJECT_VERSION,
        "symbol": inst.symbol,
        "as_of": utcnow().isoformat(),
        "decision": decision,
        "eligibility_verdict": e["verdict"],
        "qualification_verdict": e["qualification_verdict"],
        "price": price,
        "confidence": confidence,
        "confidence_note": ("gate-pass strength — NOT a probability "
                            "of positive returns"),
        "stages": {
            "quality": {
                "pass": (e["gates"]["quality"] or {}).get("pass"),
                "four_ms": gate["four_ms"],
                "five_numbers": gate["five_numbers"]},
            "valuation": {
                "pass": (e["gates"]["valuation"] or {}).get("pass"),
                "status": gate["valuation"]["status"],
                "zone": gate["valuation"]["rule1"]["zone"],
                "mos_price": gate["valuation"]["rule1"]["mos_price"],
                "sticker_price":
                    gate["valuation"]["rule1"]["sticker_price"],
                "dcf": gate["valuation"]["dcf"]["intrinsic_value"]},
            "technical": {
                "pass": tech.get("pass"),
                "decision": tech.get("decision"),
                "data_fresh": tech.get("data_fresh")},
            "margin": e["gates"].get("margin"),
            "portfolio": e["gates"].get("portfolio"),
            "sleeve": {
                "enabled": bool(sleeve.get("enabled")),
                "cooldown": bool(sleeve.get("cooldown")),
                "lifecycle": sleeve.get("lifecycle"),
                "state": sleeve.get("state")},
        },
        "sizing": e.get("sizing"),
        "cio_advisory": ({
            "verdict": dr.verdict,
            "confidence": dr.cio_confidence,
            "at": dr.at.isoformat() if dr.at else None,
            "note": ("advisory — cannot override the risk gates "
                     "above; human approval still required")}
            if dr else None),
        "blocking": e["blocking"],
        "risk_flags": sorted(set(risk_flags)),
        "approval_required": True,          # invariant — never false
        "engine_refs": {
            "decision": OBJECT_VERSION,
            "risk": re_.ENGINE_VERSION,
            "qualification": "qualification/v1.0",
        },
        "audit": {
            "eligibility_as_of": e["as_of"],
            "qualification_as_of": gate["as_of"],
        },
    }
