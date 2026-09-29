"""Universe pipeline — the OS spine. Runs the full decision chain for
every approved-universe instrument:

screening (existing results) → valuation → regime → quant →
technical → risk gate → 9-agent committee → CIO synthesis →
decision tree → decision record + human approval.

Each instrument runs run_committee which internally re-computes the
engine ctx (fresh price, valuation, regime, quant, technical, risk
gate) — the committee never reads stale snapshots."""

import asyncio
import logging
from datetime import datetime
from datetime import timezone as tz

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.models.instruments import Instrument
from app.models.universe import Universe, UniverseMembership
from app.services import committee as cs

log = logging.getLogger("vaiip.pipeline")


async def approved_universe(db: AsyncSession) -> list[Instrument]:
    """Instruments currently in the approved tier."""
    rows = (await db.execute(
        select(Instrument)
        .join(UniverseMembership,
              UniverseMembership.instrument_id == Instrument.id)
        .join(Universe, UniverseMembership.universe_id == Universe.id)
        .where(Universe.tier == "approved", Instrument.is_active)
        .where(UniverseMembership.status == "active")
    )).scalars().all()
    if rows:
        return list(rows)
    # no membership rows yet → fall back to all active instruments
    return list((await db.execute(
        select(Instrument).where(Instrument.is_active))).scalars().all())


async def run_pipeline(db: AsyncSession,
                       max_symbols: int = 24) -> dict:
    """One sequential pass over the universe — sequential because every
    stage shares the same db session and each committee run is already
    ~9 engine calls + a valuation. Returns per-symbol verdict map."""
    insts = (await approved_universe(db))[:max_symbols]
    results: list[dict] = []
    verdicts: dict[str, int] = {}
    for inst in insts:
        t0 = datetime.now(tz.utc)
        try:
            r = await cs.run_committee(db, inst)
            await db.commit()
            verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
            results.append({
                "symbol": inst.symbol, "verdict": r["verdict"],
                "confidence": r["cio"].get("confidence"),
                "decision_id": r["decision_id"],
                "ms": int((datetime.now(tz.utc) - t0).total_seconds()
                          * 1000)})
        except Exception as e:
            await db.rollback()
            log.warning("pipeline %s failed: %s", inst.symbol, e)
            results.append({"symbol": inst.symbol, "verdict": "error",
                            "error": str(e)[:200]})
    return {
        "as_of": utcnow().isoformat(),
        "universe_size": len(insts),
        "verdicts": verdicts,
        "results": results,
    }
