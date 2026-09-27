"""Part 25 — Monitoring engine + alert infrastructure.

Checks run against stored engine outputs — a failed provider yields
a data-quality alert, never a false 'no risk'. Alerts dedupe by
(source, dedup_key, instrument) within a cooldown window; resolved
history is kept on the row.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.macro import RegimeRun
from app.models.market import OhlcvBar
from app.models.ops import Alert, Job, JobRun
from app.models.portfolio import Position
from app.models.execution import BrokerOrderRec
from app.models.risk import PyramidTradeRec, RiskCheck
from app.models.screening import ScreeningResult
from app.models.instruments import Instrument

MONITOR_VERSION = "monitor/v1.0"
COOLDOWN_H = 24


async def emit_alert(
    db: AsyncSession, *, severity: str, source: str, message: str,
    dedup_key: str, instrument_id: str | None = None,
    observed=None, required=None, action: str = "review",
    fresh: bool = True,
) -> Alert | None:
    """Deduped alert insert — returns None if suppressed."""
    since = datetime.now(timezone.utc) - timedelta(hours=COOLDOWN_H)
    dup = (
        await db.execute(
            select(Alert).where(
                Alert.source == source,
                Alert.context.op("->>")("dedup_key") == dedup_key,
                Alert.status.in_(["active", "acknowledged"]),
                Alert.created_at >= since))
    ).scalar_one_or_none()
    if dup:
        return None
    a = Alert(
        severity=severity, source=source, message=message,
        context={
            "dedup_key": dedup_key, "instrument_id": instrument_id,
            "observed": observed, "required": required,
            "action": action, "data_fresh": fresh,
            "monitor_version": MONITOR_VERSION},
    )
    db.add(a)
    await db.flush()
    return a


async def run_checks(db: AsyncSession) -> dict:
    """All monitor families — count emitted per family."""
    now = datetime.now(timezone.utc)
    n = {"macro": 0, "trading": 0, "portfolio": 0,
         "valuation": 0, "fundamental": 0, "jobs": 0,
         "data_quality": 0}

    # ── macro: regime + thresholds ──
    regime = (
        await db.execute(
            select(RegimeRun).order_by(RegimeRun.as_of.desc()))
    ).scalars().first()
    if regime:
        fg = regime.fear_greed
        if fg is not None and fg >= 80:
            if await emit_alert(
                    db, severity="critical", source="monitor:macro",
                    message=f"Fear & Greed {fg} ≥80 — new positions "
                            f"restricted",
                    dedup_key="fg_extreme",
                    observed=fg, required="<80",
                    action="defer new buys"): n["macro"] += 1
        vix = regime.vix
        if vix is not None and vix >= 30:
            if await emit_alert(
                    db, severity="warning", source="monitor:macro",
                    message=f"VIX {vix:.1f} ≥30 — risk-off",
                    dedup_key="vix_high",
                    observed=vix, required="<30",
                    action="halve size / defer"): n["macro"] += 1
        if regime.market_regime == "risk_off":
            if await emit_alert(
                    db, severity="warning", source="monitor:macro",
                    message="Market regime risk_off",
                    dedup_key="regime_off",
                    observed="risk_off", required="risk_on/neutral",
                    action="reduce exposure"): n["macro"] += 1
    else:
        if await emit_alert(
                db, severity="warning", source="monitor:macro",
                message="No macro regime run — regime unknown",
                dedup_key="regime_missing",
                action="run macro ingestion"): n["macro"] += 1

    # ── trading: open pyramid trades vs stops, stuck orders ──
    trades = (
        await db.execute(
            select(PyramidTradeRec, Instrument.symbol)
            .join(Instrument,
                  PyramidTradeRec.instrument_id == Instrument.id)
            .where(PyramidTradeRec.state.not_in(
                ["closed", "stopped_out", "rejected"])))
    ).all()
    for t, sym in trades:
        bar = (
            await db.execute(
                select(OhlcvBar)
                .where(OhlcvBar.instrument_id == t.instrument_id,
                       OhlcvBar.timeframe == "1d")
                .order_by(OhlcvBar.time.desc()).limit(1))
        ).scalar_one_or_none()
        if bar and float(bar.close) < t.stop:
            if await emit_alert(
                    db, severity="critical", source="monitor:trading",
                    message=f"{sym}: price {float(bar.close):.2f} below "
                            f"stop {t.stop:.2f} ({t.state})",
                    dedup_key=f"stop_breach:{sym}:{t.state}",
                    instrument_id=t.instrument_id,
                    observed=float(bar.close), required=t.stop,
                    action="exit per stop policy"): n["trading"] += 1

    stuck = (
        await db.execute(
            select(BrokerOrderRec).where(
                BrokerOrderRec.status.in_(
                    ["unknown", "submitted", "acknowledged"])))
    ).scalars().all()
    for o in stuck:
        age_h = (now - o.created_at.replace(
            tzinfo=timezone.utc)).total_seconds() / 3600 \
            if o.created_at else 0
        if age_h > 1:
            if await emit_alert(
                    db, severity="warning", source="monitor:trading",
                    message=f"order {o.id[:8]} uncertain/stuck "
                            f"({o.status}, {age_h:.0f}h) — reconcile",
                    dedup_key=f"stuck_order:{o.id}",
                    action="reconcile broker state"): n["trading"] += 1

    # ── portfolio: denied risk checks recently ──
    denied = (
        await db.execute(
            select(func.count(RiskCheck.id)).where(
                RiskCheck.allowed == False,  # noqa: E712
                RiskCheck.checked_at >= now - timedelta(hours=24)))
    ).scalar()
    if denied and denied >= 5:
        if await emit_alert(
                db, severity="warning", source="monitor:portfolio",
                message=f"{denied} risk-check denials in 24h — "
                        f"limits under pressure",
                dedup_key="denial_storm",
                observed=denied, required="<5",
                action="review exposure"): n["portfolio"] += 1

    # ── data quality: failed jobs / stale sync ──
    failed = (
        await db.execute(
            select(JobRun).where(JobRun.status == "failed")
            .order_by(JobRun.finished_at.desc()).limit(20))
    ).scalars().all()
    seen: set[str] = set()
    for j in failed:
        if j.job_id in seen:
            continue
        seen.add(j.job_id)
        job = await db.get(Job, j.job_id)
        key = job.key if job else j.job_id[:8]
        if await emit_alert(
                db, severity="warning", source="monitor:data",
                message=f"job {key} failed — check provider",
                dedup_key=f"job_fail:{key}",
                observed="failed", required="success",
                action="inspect job run",
                fresh=False): n["data_quality"] += 1

    # ── valuation drift: watch positions where price ≥ IV (needs
    # stored IV per position — from latest decision record) ──
    from app.models.governance import DecisionRecord
    latest_iv = dict(
        (await db.execute(
            select(DecisionRecord.instrument_id,
                   func.max(DecisionRecord.at))
            .group_by(DecisionRecord.instrument_id))).all())
    for pos in (await db.execute(
            select(Position).where(Position.quantity > 0))).scalars():
        dr = (
            await db.execute(
                select(DecisionRecord)
                .where(DecisionRecord.instrument_id == pos.instrument_id,
                       DecisionRecord.at == latest_iv.get(
                           pos.instrument_id)))
        ).scalar_one_or_none()
        if dr and dr.numbers.get("iv") and dr.numbers.get("price"):
            if dr.numbers["price"] >= dr.numbers["iv"]:
                sym = (await db.execute(
                    select(Instrument.symbol).where(
                        Instrument.id == pos.instrument_id))).scalar()
                if await emit_alert(
                        db, severity="warning", source="monitor:valuation",
                        message=f"{sym}: price ≥ IV — valuation exit "
                                f"review",
                        dedup_key=f"price_ge_iv:{sym}",
                        instrument_id=pos.instrument_id,
                        observed=dr.numbers["price"],
                        required=dr.numbers["iv"],
                        action="reduce/exit review"): n["valuation"] += 1

    return {"emitted": n, "monitor_version": MONITOR_VERSION,
            "at": now.isoformat()}
