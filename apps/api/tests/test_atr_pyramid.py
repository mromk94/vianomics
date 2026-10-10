"""ATR output sheet + pyramid preview — docs Part 12.

Canonical formula (ATR Calculator.xls, verified 575/575 cells):
per-bar range% = ((H−L)ₜ + (H−L)ₜ₋₁)/2 ÷ Openₜ₋₁; Current ATR% =
mean of the last 6 daily (12 weekly / 6 monthly) values;
stop = C×(1−1.5×ATR%), target = C×(1+3×ATR%). The UI must consume
real computed ATR — never the old 3%-of-price proxy.
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


# ── workbook formula ──

def _wb_pct(bars, window):
    """Workbook per-bar range% = mean(H−L of 2 bars)/prev OPEN;
    Current ATR% = mean of last `window` values."""
    rng = [((b["high"] - b["low"]) + (bars[i - 1]["high"]
           - bars[i - 1]["low"])) / 2 / bars[i - 1]["open"]
           for i, b in enumerate(bars) if i > 0]
    return sum(rng[-window:]) / min(window, len(rng))


def test_workbook_range_series_math():
    """Cell-level formula check — the workbook's per-bar ATR% is a
    2-bar mean of High−Low over the PREVIOUS bar's open (no TR/gap
    component, no 14-period smoothing)."""
    bars = [
        {"open": 215.83, "high": 218.46, "low": 205.14,
         "close": 213.58},   # HL = 13.32
        {"open": 204.02, "high": 210.05, "low": 203.88,
         "close": 207.32},   # HL = 6.17
    ]
    # sheet cell = (6.17+13.32)/2 ÷ 215.83 = 0.04515128 — the exact
    # value the workbook stores in its Daily ATR column
    assert _wb_pct(bars, 6) == pytest.approx(0.04515127646759032)


# ── report ──

async def test_atr_report_hand_calc(db):
    await _seed(db, n=110)          # ~22 ISO weeks → weekly ATR valid too
    rep = await atr_svc.atr_report(db, "tst")   # case-insensitive
    assert rep["symbol"] == "TST"
    # fixture: H−L=4/bar, open_i = 99.5+i → range%_i = 4/(98.5+i)
    bars = _bars(110)
    pct = _wb_pct(bars, 6)
    atr_abs = bars[-1]["close"] * pct       # 209 × pct
    assert rep["daily"]["atr_pct"] == pytest.approx(pct)
    assert rep["daily"]["atr_abs"] == pytest.approx(atr_abs)
    # pyramid levels at last close — the sheet's multiplicative form
    assert rep["pyramid"]["stop"] == pytest.approx(209 - 1.5 * atr_abs)
    assert rep["pyramid"]["target"] == pytest.approx(209 + 3 * atr_abs)
    # horizon windows present once enough bars exist
    assert "6" in rep["daily"]["windows"]
    assert "24" in rep["daily"]["windows"]
    assert rep["weekly"]["atr_abs"] is not None
    assert rep["monthly"]["atr_abs"] is not None  # ~5 months ≥ 6-window min


async def test_atr_report_insufficient(db):
    await _seed(db, n=5)                    # < 6-window + prior bar
    rep = await atr_svc.atr_report(db, "TST")
    assert rep["insufficient"] is True
    assert rep["bars"] == 5


async def test_atr_report_unknown(db):
    assert await atr_svc.atr_report(db, "NOPE") is None


async def test_resample_sessions_anchors_to_newest(db):
    """2d/3d buckets count SESSIONS anchored to the last bar — the
    newest bucket is always complete; only the oldest keeps a
    history remainder (same convention as technical.aggregate)."""
    bars = _bars(31)                     # odd count → remainder first
    two = atr_svc._resample_sessions(bars, 2)
    assert len(two) == 16                # 15×2 + 1 leftover
    assert two[-1]["close"] == bars[-1]["close"]
    assert two[-1]["high"] == max(b["high"] for b in bars[-2:])
    assert two[-1]["low"] == min(b["low"] for b in bars[-2:])
    assert two[-1]["open"] == bars[-2]["open"]
    # first bucket holds the single remainder session
    assert two[0]["close"] == bars[0]["close"]
    three = atr_svc._resample_sessions(bars, 3)
    assert three[-1]["close"] == bars[-1]["close"]
    assert len(three) == 11              # 10×3 + 1 leftover


async def test_atr_report_includes_multiday_frames(db):
    """daily.sub['2d']/['3d'] run the identical workbook formula on
    n-session price buckets — same price history the bars endpoint
    serves under its 2d/3d filter."""
    await _seed(db, n=60)
    rep = await atr_svc.atr_report(db, "TST")
    bars = _bars(60)
    for n, key in ((2, "2d"), (3, "3d")):
        sub = rep["daily"]["sub"][key]
        rb = atr_svc._resample_sessions(bars, n)
        pct = _wb_pct(rb, 6)
        assert sub["atr_pct"] == pytest.approx(pct)
        assert sub["atr_abs"] == pytest.approx(
            rb[-1]["close"] * pct)
        assert sub["unit_sessions"] == n


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
    # workbook ATR for the n=30 fixture, worked out in-test
    atr_abs = _bars(30)[-1]["close"] * _wb_pct(_bars(30), 6)
    s = rep["sheet"]
    assert s["stop"] == pytest.approx(129 - 1.5 * atr_abs)
    assert s["target"] == pytest.approx(129 + 3 * atr_abs)
    assert s["risk_per_share"] == pytest.approx(1.5 * atr_abs)
    assert s["dollar_risk"] == pytest.approx(10_000)  # 1M × 1%
    assert s["shares"] == int(10_000 / (1.5 * atr_abs))
    assert s["binding"] == "risk"
    assert s["rr"] == pytest.approx(2.0)
    assert rep["legs"][1]["fill"] == pytest.approx(129 + 3 * atr_abs)
    # real workbook ATR — not the old 3% proxy (3.87) nor SMA14 (4.0)
    assert rep["atr"]["abs"] == pytest.approx(atr_abs)


async def test_pyramid_preview_insufficient_bars(db):
    from fastapi import HTTPException
    from app.routers.risk import PyramidPreviewIn, pyramid_preview
    await _seed(db, n=5)
    with pytest.raises(HTTPException) as e:
        await pyramid_preview(PyramidPreviewIn(
            symbol="TST", equity=1e6, cash=1e6), db)
    assert e.value.status_code == 422


async def test_preview_leverage_override_scales_sleeve(db):
    """The calculator's leverage slider must drive the sleeve gross
    cap — equity input already feeds sleeve_equity; leverage feeds
    target_leverage. Caps recompute on the hypothetical pair."""
    from app.models.risk import LimitConfig
    from app.routers.risk import PyramidPreviewIn, pyramid_preview
    await _seed(db)
    db.add(LimitConfig(version=1, payload={
        "sleeve_enabled": True, "min_sectors": 0}))
    await db.flush()

    rep = await pyramid_preview(PyramidPreviewIn(
        symbol="TST", equity=1_000_000, leverage=2.0), db)
    assert rep["sleeve"]["config"]["target_leverage"] == 2.0
    st = rep["sleeve"]["state"]
    assert st["sleeve_equity"] == pytest.approx(300_000)
    # 2× cap = $600k — under the maint-margin safe bound at the
    # default 16%, so it stands as the effective cap
    assert st["effective_gross_cap"] == pytest.approx(600_000)


async def test_preview_multiday_timeframe_sheet(db):
    """timeframe='2d' must make the 2-session-bucket ATR the primary
    sheet — same workbook formula, n-session price buckets."""
    from app.routers.risk import PyramidPreviewIn, pyramid_preview
    await _seed(db, n=60)
    rep = await pyramid_preview(PyramidPreviewIn(
        symbol="TST", equity=1_000_000, cash=500_000,
        timeframe="2d"), db)
    assert rep["primary_timeframe"] == "2d"
    sh2 = rep["sheets"]["2d"]
    rb = atr_svc._resample_sessions(_bars(60), 2)
    atr2 = rb[-1]["close"] * _wb_pct(rb, 6)
    assert sh2["atr_abs"] == pytest.approx(atr2)
    assert sh2["stop"] == pytest.approx(159 - 1.5 * atr2)
    assert sh2["target"] == pytest.approx(159 + 3 * atr2)


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
    atr_abs = 129 * _wb_pct(_bars(30), 6)
    assert rec.stop == pytest.approx(129 - 1.5 * atr_abs)
    assert rec.target1 == pytest.approx(129 + 3 * atr_abs)
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
