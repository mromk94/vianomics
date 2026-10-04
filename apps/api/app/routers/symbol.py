"""Consolidated symbol detail — one call for the global symbol drawer.
Aggregates instrument meta + position + screening + technical +
valuation + latest decision + dossier availability so every ticker
click in the UI opens a rich view without 6 round trips."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.session import get_db
from app.models.governance import DecisionRecord
from app.models.instruments import Instrument, Sector
from app.models.dossier import Dossier, DossierSection
from app.models.screening import ScreeningResult
from app.routers.risk import _portfolio_ctx
from app.services import technical_engine as te
from app.services.valuation_engine import METHODOLOGY_VERSION

router = APIRouter(prefix="/symbol", tags=["symbol"])


@router.get("/{symbol}")
async def symbol_detail(symbol: str, db: AsyncSession = Depends(get_db)) -> dict:
    sym = symbol.upper().split(".")[0]
    inst = (await db.execute(
        select(Instrument).where(Instrument.symbol == sym))
    ).scalar_one_or_none()

    sec = exch = None
    if inst and inst.sector_id:
        s = await db.get(Sector, inst.sector_id)
        sec = s.name if s else None
    if inst and inst.exchange_id:
        from app.models.instruments import Exchange
        e = await db.get(Exchange, inst.exchange_id)
        exch = e.code if e else None

    # position if held (internal or external)
    ctx = await _portfolio_ctx(db)
    pos = next((p for p in ctx["positions"]
                if p["symbol"] == sym
                or p.get("display_symbol", "").upper() == sym
                or (p.get("display_symbol") or "").split(".")[0] == sym),
               None)

    # screening
    scr = None
    if inst:
        r = (await db.execute(
            select(ScreeningResult)
            .where(ScreeningResult.instrument_id == inst.id)
            .order_by(ScreeningResult.created_at.desc())
        )).scalars().first()
        if r:
            scr = {"score": r.score, "verdict": r.verdict,
                   "at": r.created_at.isoformat() if r.created_at
                   else None}

    # latest decision
    dec = None
    if inst:
        d = (await db.execute(
            select(DecisionRecord)
            .where(DecisionRecord.instrument_id == inst.id)
            .order_by(DecisionRecord.at.desc())
        )).scalars().first()
        if d:
            dec = {"verdict": d.verdict, "at": d.at.isoformat(),
                   "confidence": d.cio_confidence,
                   "numbers": d.numbers}

    # technical signal (live compute — bars already fetched/cached)
    tech = None
    if inst:
        try:
            t = await te.evaluate(db, inst, utcnow())
            tech = {
                "decision": t.get("decision"),
                "mean_reversion": (t.get("mean_reversion") or {})
                                   .get("decision"),
                "trend_following": (t.get("trend_following") or {})
                                    .get("decision"),
                "last_close": t.get("last_close"),
                "atr_14": (t.get("indicators") or {}).get("atr_14"),
                "data_fresh": t.get("data_fresh"),
                "freshness_note": t.get("freshness_note"),
            }
        except Exception:
            pass

    # latest valuation
    val = None
    if inst:
        try:
            from app.models.valuation import ValuationRun
            v = (await db.execute(
                select(ValuationRun)
                .where(ValuationRun.instrument_id == inst.id)
                .order_by(ValuationRun.created_at.desc())
            )).scalars().first()
            if v:
                o = v.outputs or {}
                val = {"sticker": o.get("sticker")
                       or (o.get("rule1") or {}).get("sticker_price"),
                       "buy": o.get("buy")
                       or (o.get("rule1") or {}).get("buy_price"),
                       "fair": (o.get("dcf") or {}).get("per_share"),
                       "engine": v.methodology or METHODOLOGY_VERSION,
                       "at": v.created_at.isoformat()
                       if v.created_at else None}
        except Exception:
            pass

    # dossier availability
    dossier = None
    if inst:
        d = (await db.execute(
            select(Dossier)
            .where(Dossier.instrument_id == inst.id)
            .order_by(Dossier.created_at.desc())
        )).scalars().first()
        if d:
            nsec = (await db.execute(
                select(func.count()).select_from(DossierSection)
                .where(DossierSection.dossier_id == d.id)
            )).scalar()
            dossier = {"version": d.version,
                       "at": d.created_at.isoformat()
                       if d.created_at else None,
                       "sections": nsec}

    # market-intelligence signal surface (same math as /market/signals)
    sig = None
    if inst:
        try:
            from app.services import market_signals as ms
            sig = await ms.signal_row(db, inst)
        except Exception:
            pass

    # latest fundamentals per concept (SEC facts)
    fundamentals = []
    if inst:
        try:
            from app.models.fundamentals import FundamentalObservation as FO
            rn = func.row_number().over(
                partition_by=FO.concept,
                order_by=(FO.period_end.desc(), FO.published_at.desc())
            ).label("rn")
            sub = select(FO.concept, FO.period_end, FO.value,
                         FO.unit, rn).where(
                FO.instrument_id == inst.id).subquery()
            fundamentals = [
                {"concept": r.concept,
                 "period_end": (r.period_end.isoformat()
                                if r.period_end else None),
                 "value": float(r.value) if r.value is not None else None,
                 "unit": r.unit}
                for r in (await db.execute(
                    select(sub).where(sub.c.rn == 1))).all()]
            fundamentals.sort(key=lambda f: f["concept"])
        except Exception:
            pass

    # open pyramid records for this instrument
    pyramids = []
    if inst:
        from app.models.risk import PyramidTradeRec
        pyramids = [
            {"id": r.id, "state": r.state, "entry": r.entry,
             "shares": r.shares, "stop": r.stop, "target1": r.target1,
             "additions": r.additions,
             "atr_current": (r.params or {}).get("atr_current")}
            for r in (await db.execute(
                select(PyramidTradeRec).where(
                    PyramidTradeRec.instrument_id == inst.id,
                    PyramidTradeRec.state.not_in(
                        ["closed", "stopped_out", "rejected"]))
                .order_by(PyramidTradeRec.created_at.desc()))
            ).scalars().all()]

    return {
        "symbol": sym,
        "in_master": inst is not None,
        "name": inst.name if inst else None,
        "sector": sec,
        "exchange": exch if exch else None,
        "asset_class": inst.asset_class if inst else None,
        "position": pos,
        "screening": scr,
        "decision": dec,
        "technical": tech,
        "valuation": val,
        "dossier": dossier,
        "signals": sig,
        "fundamentals": fundamentals,
        "pyramids": pyramids,
    }
