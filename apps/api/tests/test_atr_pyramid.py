"""ATR output sheet + pyramid preview — docs Part 12.

The docs define TR = max(H−L, |H−prevC|, |L−prevC|), ATR% = SMA14(TR)/C,
stop = 1.5×ATR, target = 3×ATR. The UI must consume real computed ATR —
never the old 3%-of-price proxy.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.instruments import Instrument
from app.models.market import OhlcvBar
from app.services import atr as atr_svc


def _bars(n=30, step=1.0):
    """n daily bars, drifting up — deterministic H/L/C for hand-calc."""
    out = []
    for i in range(n):
        c = 100 + i * step
        out.append({
            "time": datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=i),
            "open": c - 0.5, "high": c + 2, "low": c - 2, "close": c,
        })
    return out


async def _seed(db, n=30, step=1.0):
    inst = Instrument(symbol="TST", name="Test", asset_class="equity",
                      avg_dollar_volume_30d=1e8)
    db.add(inst)
    await db.flush()
    for b in _bars(n, step):
        db.add(OhlcvBar(instrument_id=inst.id, timeframe="1d",
                        **b, volume=1_000_000, source="yahoo"))
    await db.flush()
    return inst


# ── true range ──

def test_tr_series_math():
    bars = [
        {"high": 10, "low": 8, "close": 9},
        {"high": 12, "low": 9, "close": 11},   # gap up: |12−9|=3 → TR=3
        {"high": 11, "low": 6, "close": 7},    # gap dn: |6−11|=5 → TR=5
    ]
    trs = atr_svc._tr_series(bars)
    assert trs == [2.0, 3.0, 5.0]


# ── report ──

async def test_atr_report_hand_calc(db):
    await _seed(db, n=110)          # ~22 ISO weeks → weekly ATR valid too
    rep = await atr_svc.atr_report(db, "tst")   # case-insensitive
    assert rep["symbol"] == "TST"
    # each bar: H−L=4; gap terms |H−prevC|=|c+2−(c−1)|=3, |L−prevC|=1 → TR=4
    assert rep["daily"]["atr_abs"] == pytest.approx(4.0)
    # atr% = 4 / last_close(209)
    assert rep["daily"]["atr_pct"] == pytest.approx(4.0 / 209)
    # pyramid levels at last close
    assert rep["pyramid"]["stop"] == pytest.approx(209 - 6)
    assert rep["pyramid"]["target"] == pytest.approx(209 + 12)
    # horizon windows present once enough bars exist
    assert "6" in rep["daily"]["windows"]
    assert "24" in rep["daily"]["windows"]
    assert rep["weekly"]["atr_abs"] is not None
    assert rep["monthly"]["atr_abs"] is None   # ~5 months < 14 period


async def test_atr_report_insufficient(db):
    await _seed(db, n=10)
    rep = await atr_svc.atr_report(db, "TST")
    assert rep["insufficient"] is True
    assert rep["bars"] == 10


async def test_atr_report_unknown(db):
    assert await atr_svc.atr_report(db, "NOPE") is None


async def test_resample_weekly_groups(db):
    bars = _bars(20)
    weekly = atr_svc._resample(bars, "1w")
    assert len(weekly) < len(bars)
    # each weekly bar's high = max of group, close = group's last close
    assert weekly[0]["high"] == max(
        b["high"] for b in bars
        if (b["time"].isocalendar()[:2] == weekly[0]["time"].isocalendar()[:2]))
    assert weekly[-1]["close"] == bars[-1]["close"]


# ── preview endpoint ──

async def test_pyramid_preview_real_atr(db):
    from app.routers.risk import PyramidPreviewIn, pyramid_preview
    await _seed(db)
    body = PyramidPreviewIn(symbol="tst", risk_pct=0.01,
                            equity=1_000_000, cash=500_000)
    rep = await pyramid_preview(body, db)
    s = rep["sheet"]
    assert s["stop"] == pytest.approx(123)          # 129 − 1.5×4
    assert s["target"] == pytest.approx(141)        # 129 + 3×4
    assert s["risk_per_share"] == pytest.approx(6)
    assert s["dollar_risk"] == pytest.approx(10_000)  # 1M × 1%
    assert s["shares"] == 1666                       # 10k/6
    assert s["binding"] == "risk"
    assert s["rr"] == pytest.approx(2.0)
    assert rep["legs"][1]["fill"] == pytest.approx(141)
    # real ATR, not the old 3% proxy (3.87): 4.0 is SMA14(TR)
    assert rep["atr"]["abs"] == pytest.approx(4.0)


async def test_pyramid_preview_insufficient_bars(db):
    from fastapi import HTTPException
    from app.routers.risk import PyramidPreviewIn, pyramid_preview
    await _seed(db, n=10)
    with pytest.raises(HTTPException) as e:
        await pyramid_preview(PyramidPreviewIn(
            symbol="TST", equity=1e6, cash=1e6), db)
    assert e.value.status_code == 422


# ── candidate seeding ──

async def _seed_scan(db, inst, decision="entry_signal", close=129.0):
    from app.models.market import TechnicalScanResult
    db.add(TechnicalScanResult(
        instrument_id=inst.id, decision=decision,
        engine="trend_following", indicators={}, last_close=close,
        data_fresh=True))
    await db.flush()


async def test_seed_creates_trade_eligible_candidate(db):
    from app.models.portfolio import LedgerEntry, Portfolio
    from app.services import pyramid_seed
    from app.models.risk import PyramidTradeRec
    inst = await _seed(db)
    pf = Portfolio(name="main")
    db.add(pf)
    await db.flush()
    db.add(LedgerEntry(portfolio_id=pf.id, amount=1_000_000,
                       kind="deposit"))
    await _seed_scan(db, inst)

    res = await pyramid_seed.seed_candidates(db)
    assert res["created"] == 1
    rec = (await db.execute(
        select(PyramidTradeRec).where(
            PyramidTradeRec.instrument_id == inst.id))).scalar_one()
    assert rec.state == "trade_eligible"
    assert rec.stop == pytest.approx(129 - 6)    # 1.5 × ATR(4)
    assert rec.target1 == pytest.approx(129 + 12)
    assert rec.events[0]["event"] == "candidate"

    # idempotent — second seed creates nothing
    assert (await pyramid_seed.seed_candidates(db))["created"] == 0


async def test_seed_expires_decayed_candidate(db):
    from app.models.portfolio import LedgerEntry, Portfolio
    from app.services import pyramid_seed
    from app.models.risk import PyramidTradeRec
    inst = await _seed(db)
    pf = Portfolio(name="main")
    db.add(pf)
    await db.flush()
    db.add(LedgerEntry(portfolio_id=pf.id, amount=1_000_000,
                       kind="deposit"))
    await _seed_scan(db, inst)
    await pyramid_seed.seed_candidates(db)

    # signal decays → candidate closes with an audit event
    await _seed_scan(db, inst, decision="wait")
    res = await pyramid_seed.seed_candidates(db)
    assert res["expired"] == 1
    rec = (await db.execute(
        select(PyramidTradeRec).where(
            PyramidTradeRec.instrument_id == inst.id))).scalar_one()
    assert rec.state == "closed"
    assert rec.events[-1]["event"] == "candidate_expired"


async def test_pyramids_list_includes_atr(db):
    from app.models.risk import PyramidTradeRec
    from app.routers.risk import list_pyramids
    inst = await _seed(db)
    db.add(PyramidTradeRec(
        instrument_id=inst.id, portfolio_id=None, state="initial",
        entry=100, atr_initial=4.0, shares=100, stop=94, target1=112,
        t2_policy="continue", additions=1, engine_version="v1",
        params={"atr_current": 5.0}, events=[{"event": "add"}]))
    await db.flush()
    rows = await list_pyramids(db)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "TST"
    assert rows[0]["atr_current"] == 5.0
    assert rows[0]["additions"] == 1
