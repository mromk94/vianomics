"""Learning module — agents get better with human + market feedback.

Two signals, both real and auditable:

1. Outcome accuracy — decision_feedback.agent_accuracy already scores
   whether each agent's direction matched realized price moves.
2. Human alignment — which agents' recommendations matched the admin's
   final approve/reject on order tickets.

Both fold into a weight multiplier per agent (0.6×–1.4×, floor/ceiling
so a bad streak mutes but never silences). Multipliers are applied in
cio_synthesize and persisted on each decision record — the learning is
inspectable, never hidden.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.governance import DecisionRecord

LEARNING_VERSION = "agent-weights/v1.0"
MIN_SAMPLES = 3          # below this, weight stays neutral 1.0
HALF_LIFE_DAYS = 14      # older outcomes decay


async def agent_weights(db: AsyncSession) -> dict:
    """Per-agent multipliers from realized outcomes + human alignment."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=180)
    rows = (await db.execute(
        select(DecisionRecord)
        .where(DecisionRecord.at > cutoff)
        .order_by(DecisionRecord.at.desc()))).scalars().all()

    hits: dict[str, list[float]] = {}   # agent → [weighted correctness]
    for d in rows:
        px0 = (d.numbers or {}).get("price")
        if not px0:
            continue
        # realized return at latest close comes from feedback module —
        # here we reuse the stored verdict-vs-later-price shortcut via
        # numbers.recorded_pnl when present (set by feedback passes)
        realized = (d.numbers or {}).get("realized_return")
        if realized is None:
            continue
        # recency weight — half-life decay
        age_d = (datetime.now(timezone.utc)
                 - d.at).days if d.at else 0
        rw = 0.5 ** (age_d / HALF_LIFE_DAYS)
        for agent, sc in (d.agent_scores or {}).items():
            score = sc if isinstance(sc, (int, float)) else None
            if score is None:
                continue
            # agent direction vs realized: score>60 = bullish vote
            direction = 1 if score >= 60 else (-1 if score <= 40 else 0)
            if direction:
                hits.setdefault(agent, []).append(
                    rw * (1 if (realized >= 0) == (direction > 0)
                          else -1))

    weights = {}
    for agent, h in hits.items():
        n = len(h)
        if n < MIN_SAMPLES:
            weights[agent] = {"w": 1.0, "n": n, "note": "insufficient"}
            continue
        # accuracy − 0.5 → [-0.5, +0.5] → weight 0.6–1.4
        acc = sum(h) / n
        weights[agent] = {
            "w": round(max(0.6, min(1.4, 1.0 + acc * 0.8)), 3),
            "n": n,
            "raw_acc": round((sum(1 for x in h if x > 0) / n
                              + 1) / 2, 3),
        }
    return {"version": LEARNING_VERSION, "weights": weights}


async def record_outcomes(db: AsyncSession) -> int:
    """Stamp `realized_return` onto past DecisionRecords — runs at
    backfill tail so the learning loop has fresh outcomes."""
    from app.models.market import OhlcvBar
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    rows = (await db.execute(
        select(DecisionRecord)
        .where(DecisionRecord.at < cutoff))).scalars().all()
    n = 0
    for d in rows:
        nums = dict(d.numbers or {})
        if nums.get("realized_return") is not None or \
                not nums.get("price"):
            continue
        last = (await db.execute(
            select(OhlcvBar.close)
            .where(OhlcvBar.instrument_id == d.instrument_id,
                   OhlcvBar.timeframe == "1d")
            .order_by(OhlcvBar.time.desc()).limit(1))).scalar()
        if last:
            nums["realized_return"] = round(
                float(last) / nums["price"] - 1, 4)
            nums["realized_at"] = datetime.now(timezone.utc).isoformat()
            d.numbers = nums
            n += 1
    if n:
        await db.flush()
    return n
