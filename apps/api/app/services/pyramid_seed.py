"""Pyramid candidate seeding — the docs' state machine has
WATCHLIST → TRADE ELIGIBLE → POSITION 1 before any trade exists.

When the technical engine's persisted scan fires `entry_signal`, a
trade-eligible pyramid record is created with REAL ATR levels so the
Risk Engine tab shows the candidate pipeline — the signal → level →
approval chain — instead of an empty section.

Nothing here stages or routes orders: promotion to POSITION 1 still
requires the human-approved POST /risk/pyramid path. Candidates whose
signal decays are closed out (event logged), keeping the pipeline
honest.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.models.instruments import Instrument
from app.models.market import TechnicalScanResult
from app.models.risk import PyramidTradeRec
from app.services import atr as atr_svc
from app.services import risk_engine as re_
from app.services.risk_engine import PyramidState

OPEN_STATES = {s.value for s in PyramidState} - {
    PyramidState.CLOSED.value, PyramidState.STOPPED.value}


async def _latest_scan(db: AsyncSession) -> dict[str, TechnicalScanResult]:
    """Latest persisted scan verdict per instrument."""
    rn = func.row_number().over(
        partition_by=TechnicalScanResult.instrument_id,
        order_by=TechnicalScanResult.created_at.desc()).label("rn")
    sub = select(TechnicalScanResult, rn).subquery()
    rows = (await db.execute(select(sub).where(sub.c.rn == 1))).all()
    return {r.instrument_id: r for r in rows}


async def seed_candidates(db: AsyncSession) -> dict:
    """Create/expire trade-eligible pyramid candidates from the latest
    persisted technical scan. Idempotent — one open candidate per
    instrument."""
    scans = await _latest_scan(db)
    if not scans:
        return {"created": 0, "expired": 0, "note": "no scan results"}

    open_recs = (await db.execute(
        select(PyramidTradeRec).where(
            PyramidTradeRec.state.in_(OPEN_STATES)))).scalars().all()
    open_by_inst = {r.instrument_id: r for r in open_recs}

    created = expired = 0
    now = utcnow().isoformat()

    # expire trade-eligible candidates whose signal decayed
    for rec in open_recs:
        if rec.state != PyramidState.ELIGIBLE.value:
            continue
        sc = scans.get(rec.instrument_id)
        if sc is None or sc.decision != "entry_signal":
            rec.state = PyramidState.CLOSED.value
            rec.events = [*(rec.events or []),
                          {"t": now, "event": "candidate_expired",
                           "reason": "signal decayed"}]
            expired += 1

    from app.routers.risk import _portfolio_ctx
    ctx = await _portfolio_ctx(db)

    for inst_id, sc in scans.items():
        if sc.decision != "entry_signal" or inst_id in open_by_inst:
            continue
        inst = await db.get(Instrument, inst_id)
        if inst is None or not sc.last_close:
            continue
        rep = await atr_svc.atr_report(db, inst.symbol)
        if rep is None or rep.get("insufficient"):
            continue
        atr_abs = rep["daily"]["atr_abs"]
        if not atr_abs or not ctx["nav"]:
            continue
        adv = (float(inst.avg_dollar_volume_30d) / sc.last_close
               if inst.avg_dollar_volume_30d else None)
        t = re_.create_pyramid(
            inst.symbol, ctx["nav"], sc.last_close, atr_abs,
            ctx["cash"], adv_shares=adv)
        events = [{"t": now, "event": "candidate",
                   "engine": sc.engine,
                   "entry": sc.last_close,
                   "atr": atr_abs,
                   "note": "trade-eligible — awaiting PM approval"},
                  *t.events]
        db.add(PyramidTradeRec(
            instrument_id=inst.id,
            state=PyramidState.ELIGIBLE.value,
            entry=t.entry, atr_initial=t.atr_initial,
            shares=t.shares, stop=t.stop, target1=t.target1,
            t2_policy=t.t2_policy or "adaptive",
            engine_version=re_.ENGINE_VERSION, events=events,
            params={"risk_pct": 0.005, "leg_shares": t.leg_shares,
                    "atr_current": t.atr_current,
                    "signal_engine": sc.engine,
                    "source": "technical_scan"}))
        created += 1

    if created or expired:
        await db.commit()
    return {"created": created, "expired": expired}
