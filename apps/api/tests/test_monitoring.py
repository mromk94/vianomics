"""Part 25 — alert dedup, thresholds, staleness."""

import pytest
from datetime import datetime, timedelta, timezone

from app.models.macro import RegimeRun
from app.models.ops import Alert
from app.services import monitoring as mon
from sqlalchemy import select


@pytest.mark.asyncio
async def test_alert_emits_with_full_context(db):
    a = await mon.emit_alert(
        db, severity="warning", source="monitor:test",
        message="test alert", dedup_key="k1",
        observed=10, required="<5", action="review")
    assert a is not None
    assert a.context["observed"] == 10
    assert a.context["required"] == "<5"
    assert a.context["monitor_version"] == mon.MONITOR_VERSION


@pytest.mark.asyncio
async def test_alert_dedup_within_cooldown(db):
    a1 = await mon.emit_alert(db, severity="warning",
                              source="monitor:test", message="x",
                              dedup_key="dup1")
    a2 = await mon.emit_alert(db, severity="warning",
                              source="monitor:test", message="x again",
                              dedup_key="dup1")
    assert a1 is not None and a2 is None     # suppressed


@pytest.mark.asyncio
async def test_resolved_alert_allows_new(db):
    a = await mon.emit_alert(db, severity="info", source="monitor:test",
                             message="x", dedup_key="dup2")
    a.status = "resolved"
    a.resolved_at = datetime.now(timezone.utc)
    await db.flush()
    a2 = await mon.emit_alert(db, severity="info", source="monitor:test",
                              message="y", dedup_key="dup2")
    assert a2 is not None                    # resolved → not suppressed


@pytest.mark.asyncio
async def test_fg_extreme_fires(db):
    db.add(RegimeRun(
        as_of=datetime.now(timezone.utc), econ_regime="expansion",
        market_regime="risk_on", fear_greed=85, overlay="reduce",
        vix=15.0, rules_version="r1", features={}, rule_hits=[]))
    res = await mon.run_checks(db)
    assert res["emitted"]["macro"] >= 1
    row = (await db.execute(select(Alert).where(
        Alert.context.op("->>")("dedup_key") == "fg_extreme"))
    ).scalar_one()
    assert row.severity == "critical"
    assert row.context["observed"] == 85


@pytest.mark.asyncio
async def test_vix_and_risk_off_alerts(db):
    db.add(RegimeRun(
        as_of=datetime.now(timezone.utc), econ_regime="slowdown",
        market_regime="risk_off", fear_greed=20, overlay="reduce",
        vix=35.0, rules_version="r1", features={}, rule_hits=[]))
    res = await mon.run_checks(db)
    assert res["emitted"]["macro"] >= 2      # vix_high + regime_off


@pytest.mark.asyncio
async def test_missing_regime_is_alert_not_silence(db):
    res = await mon.run_checks(db)
    # no RegimeRun → 'regime_missing' data-quality alert
    row = (await db.execute(select(Alert).where(
        Alert.context.op("->>")("dedup_key") == "regime_missing"))
    ).scalar_one_or_none()
    assert row is not None
    assert "unknown" in row.message
