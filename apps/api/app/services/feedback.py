"""Part 32 — Feedback & learning: realized outcomes vs predictions.

Compares decision records' expected outcome (MOS → expected return)
with realized moves; scores each agent's recommendation against the
realized sign. Read-only learning — never auto-changes limits,
mandates or live strategies (proposed changes require backtest +
human review)."""

from datetime import datetime, timedelta, timezone
from math import sqrt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.governance import DecisionRecord
from app.models.instruments import Instrument
from app.models.market import OhlcvBar

FEEDBACK_VERSION = "feedback/v1.0"


async def _last_close(db, instrument_id) -> tuple[float | None, str | None]:
    bar = (
        await db.execute(
            select(OhlcvBar.close, OhlcvBar.time)
            .where(OhlcvBar.instrument_id == instrument_id,
                   OhlcvBar.timeframe == "1d")
            .order_by(OhlcvBar.time.desc()).limit(1))
    ).first()
    return (float(bar[0]), bar[1].isoformat()) if bar else (None, None)


async def decision_feedback(
    db: AsyncSession, min_age_days: int = 1,
) -> dict:
    """Each decision: expected return ≈ upside to IV (MOS-derived);
    realized = price now vs price at decision."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=min_age_days)
    rows = (
        await db.execute(
            select(DecisionRecord, Instrument.symbol)
            .join(Instrument,
                  DecisionRecord.instrument_id == Instrument.id)
            .where(DecisionRecord.at < cutoff)
            .order_by(DecisionRecord.at.desc()))
    ).all()

    per_decision = []
    agent_hits: dict[str, list[bool]] = {}
    for d, sym in rows:
        px0 = (d.numbers or {}).get("price")
        iv = (d.numbers or {}).get("iv")
        last, last_t = await _last_close(db, d.instrument_id)
        if not px0 or not last:
            continue
        realized = last / px0 - 1
        expected = (iv / px0 - 1) if iv else None
        # direction: verdict oriented?
        expected_dir = None
        if d.verdict in ("approve", "approve_pending_human"):
            expected_dir = 1
        elif d.verdict == "no_trade":
            expected_dir = -1
        direction_correct = (
            (realized >= 0) == (expected_dir > 0)
            if expected_dir else None)
        per_decision.append({
            "symbol": sym, "at": d.at.isoformat(),
            "verdict": d.verdict,
            "expected_return": round(expected, 4) if expected else None,
            "realized_return": round(realized, 4),
            "direction_correct": direction_correct,
            "as_of": last_t,
        })
        for agent, info in (d.agent_scores or {}).items():
            rec = info.get("rec") if isinstance(info, dict) else None
            if rec is None:
                continue
            agent_dir = 1 if rec in ("BUY", "STRONG BUY") else \
                (-1 if rec in ("SELL", "BLOCK") else 0)
            if agent_dir:
                agent_hits.setdefault(agent, []).append(
                    (realized >= 0) == (agent_dir > 0))

    agent_acc = {
        a: {"n": len(h), "accuracy": round(sum(h) / len(h), 3)}
        for a, h in agent_hits.items()}

    n_dir = [x for x in per_decision if x["direction_correct"] is not None]
    acc = (sum(1 for x in n_dir if x["direction_correct"]) / len(n_dir)
           if n_dir else None)
    exp_pairs = [(x["expected_return"], x["realized_return"])
                 for x in per_decision
                 if x["expected_return"] is not None]
    mse = (sum((e - r) ** 2 for e, r in exp_pairs) / len(exp_pairs)
           if exp_pairs else None)

    return {
        "version": FEEDBACK_VERSION,
        "decisions": per_decision,
        "summary": {
            "n": len(per_decision),
            "direction_accuracy": round(acc, 3) if acc else None,
            "expected_vs_realized_mse": round(mse, 4) if mse else None,
            "note": "realized = latest close vs decision-date price; "
                    "not P&L — actual realized P&L requires broker "
                    "fills"},
        "agent_accuracy": agent_acc,
        "policy": "feedback never auto-changes limits, mandates or "
                  "live strategies — proposed changes need backtest + "
                  "validation + human approval",
    }


async def trade_attribution(db: AsyncSession) -> dict:
    """Realized P&L attribution from persisted broker orders — paper
    fills counted, clearly labeled."""
    from app.models.execution import BrokerOrderRec
    from app.models.instruments import Instrument

    buys = (
        await db.execute(
            select(BrokerOrderRec, Instrument.symbol)
            .join(Instrument,
                  BrokerOrderRec.instrument_id == Instrument.id)
            .where(BrokerOrderRec.side == "buy",
                   BrokerOrderRec.status == "filled"))
    ).all()
    out = []
    for o, sym in buys:
        last, _ = await _last_close(db, o.instrument_id)
        if o.avg_fill_price and last:
            unrealized = (last - o.avg_fill_price) * o.filled_qty
            out.append({
                "symbol": sym, "broker": o.broker, "qty": o.filled_qty,
                "avg_fill": o.avg_fill_price, "last": round(last, 2),
                "unrealized_pnl": round(unrealized, 2),
                "source": "paper fill" if o.broker == "paper"
                else "broker fill"})
    return {"positions": out, "version": FEEDBACK_VERSION,
            "note": "unrealized marks from last close — realized P&L "
                    "requires sell fills"}
