from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_db
from app.models.ops import SyncStatus
from app.models.providers import DataProvider
from app.routers.health import _check_postgres, _check_redis
from app.schemas.command_center import (
    AgentVote,
    AlertItem,
    ApprovalItem,
    CalendarEvent,
    CioBlock,
    CommandCenterResponse,
    DecisionItem,
    PortfolioSplit,
    PortfolioSummary,
    ProviderHealth,
    RegimeSnapshot,
    RiskUtilization,
    SectorPerf,
    TradeSignal,
    WatchlistItem,
)
from app.services import demo_fixtures as demo

router = APIRouter()


async def _provider_health(db: AsyncSession) -> list[ProviderHealth]:
    """Registered providers + last successful sync — real DB state."""
    providers = (await db.execute(select(DataProvider))).scalars().all()
    latest = dict(
        (await db.execute(
            select(
                SyncStatus.provider_id,
                func.max(SyncStatus.last_success_at),
            ).group_by(SyncStatus.provider_id)
        )).all()
    )
    return [
        ProviderHealth(
            name=p.name,
            status=(
                "up" if p.status == "connected"
                else "down" if p.status == "down"
                else "degraded" if p.status == "degraded"
                else "unconfigured"
            ),
            last_sync=(
                latest[p.id].isoformat() if latest.get(p.id) else None
            ),
        )
        for p in providers
    ]


async def _system_health(db: AsyncSession | None) -> list[ProviderHealth]:
    """Real health — infrastructure checks + provider registry rows.
    External providers only ever say 'up' after a real successful sync."""
    pg = await _check_postgres()
    rd = await _check_redis()
    now = datetime.now(UTC).isoformat()
    providers = [
        ProviderHealth(
            name="PostgreSQL",
            status="up" if pg["status"] == "up" else "down",
            last_sync=now if pg["status"] == "up" else None,
            detail=pg.get("error"),
        ),
        ProviderHealth(
            name="Redis",
            status="up" if rd["status"] == "up" else "down",
            last_sync=now if rd["status"] == "up" else None,
            detail=rd.get("error"),
        ),
    ]
    if db is not None:
        try:
            providers.extend(await _provider_health(db))
        except Exception:  # noqa: BLE001 — DB must be reachable for this anyway
            pass
    return providers


@router.get("/command-center", response_model=CommandCenterResponse)
async def command_center(
    source: str = Query(
        "internal",
        # any ExternalAccount.source slug is valid — hardcoding
        # 'mt4|bamboo' 422'd the moment ibkr/alpaca connected
        pattern="^[a-z0-9_:-]{1,32}$"),
    db: AsyncSession = Depends(get_db),
) -> CommandCenterResponse:
    settings = get_settings()
    from app.routers.settings import runtime_flag
    from app.services import cache
    demo_on = await runtime_flag(db, "demo_fixtures",
                                 settings.demo_fixtures)
    cached = cache.get(f"command_center:{source}:{demo_on}", 60)
    if cached is not None:
        return cached
    providers = await _system_health(db)

    # ── real state first — each section populates from stored data;
    # demo fixtures only fill sections that have no real source ──

    # ── real state — populate each section from stored data; only
    # sections with no real source fall back to demo fixtures ──
    from app.models.agents import AgentOutput, AgentRun
    from app.models.execution import BrokerOrderRec
    from app.models.governance import Approval, DecisionRecord
    from app.models.instruments import Instrument, Sector
    from app.models.macro import RegimeRun
    from app.models.ops import Alert, Job, JobRun
    from app.routers.risk import _portfolio_ctx
    from app.models.market import OhlcvBar
    from app.services import risk_engine as re_

    # macro regime
    regime = RegimeSnapshot()
    rg = (
        await db.execute(
            select(RegimeRun).order_by(RegimeRun.as_of.desc()))
    ).scalars().first()
    if rg:
        regime = RegimeSnapshot(
            economic_regime=rg.econ_regime,
            market_regime=rg.market_regime,
            fear_greed=rg.fear_greed,
            vix=rg.vix,
            macro_indicators={
                "overlay": rg.overlay or "",
                "as_of": rg.as_of.isoformat() if rg.as_of else ""},
            sector_preferences=rg.sector_preferences or {})
    else:
        # no persisted run — compute live (same as /macro/current)
        try:
            from app.services import macro_regime as mr
            from app.providers.market import fetch_fear_greed
            c = await mr.classify(db, datetime.now(UTC),
                                  fetch_fg=fetch_fear_greed)
            rg_live = c
            f = c.get("features", {})
            def _fv(key: str) -> str:
                v = (f.get(key) or {}).get("value")
                return f"{v:g}" if isinstance(v, (int, float)) else "—"
            regime = RegimeSnapshot(
                economic_regime=c["econ_regime"],
                market_regime=c["market_regime"],
                fear_greed=c["fear_greed"],
                vix=c.get("vix"),
                macro_indicators={
                    "overlay": str(c.get("overlay", "")),
                    "fed funds": _fv("FEDFUNDS"),
                    "cpi": _fv("CPIAUCSL"),
                    "unrate": _fv("UNRATE"),
                    "yield curve": _fv("T10Y2Y"),
                    "vix": _fv("VIXCLS"),
                    "stale": f"{len(c.get('stale_inputs', []))} series"},
                sector_preferences=c.get("sector_preferences") or {})
            rg = type("Rg", (), {"sector_preferences":
                                 c.get("sector_preferences", {})})()
        except Exception as e:
            import logging
            logging.warning("CC regime fallback failed: %s", e)
            rg_live = None

    # portfolio / risk
    pf = await _portfolio_ctx(db)
    from app.routers.risk import _active_limits
    cc_limits = await _active_limits(db)
    dims = re_.risk_dimensions(pf, limits=cc_limits)

    # VaR95 + MDD from real equity history (external accounts carry
    # rolling snapshots; internal book has no nav history yet)
    from app.models.portfolio import ExternalAccount
    ext_accs = (await db.execute(
        select(ExternalAccount).where(ExternalAccount.connected))
    ).scalars().all()
    stats = [re_.equity_stats(a.equity_history or [],
                              float(a.equity) if a.equity else None)
             for a in ext_accs]
    if stats:
        pf["max_dd"] = pf.get("max_dd") or min(
            (x["mdd_pct"] for x in stats
             if x["mdd_pct"] is not None), default=None)
        pf["_var95"] = sum(x["var_95"] or 0 for x in stats) or None

    # avg pairwise correlation across held symbols with bars
    held = [p["symbol"] for p in pf["positions"] if not p.get("external")
            or p.get("instrument_matched")]
    held = [s_ for s_ in held if not s_.startswith("MT4 #")]
    if len(held) >= 2:
        try:
            from app.services.quant_service import correlation_matrix
            cm = await correlation_matrix(db, held)
            vals = [v for i, row in enumerate(cm["matrix"])
                    for j, v in enumerate(row)
                    if i != j and v is not None]
            pf["avg_correlation"] = (sum(vals) / len(vals)
                                     if vals else None)
        except Exception:
            pass
    conc = dims.get("concentration", {})
    risk = RiskUtilization(
        drawdown_pct=(pf.get("max_dd") * 100
                      if pf.get("max_dd") is not None else None),
        max_sector_pct=(conc.get("max_sector_pct") * 100
                        if conc.get("max_sector_pct") is not None
                        else None),
        max_position_pct=(conc.get("max_single_name_pct") * 100
                          if conc.get("max_single_name_pct") is not None
                          else None),
        avg_correlation=pf.get("avg_correlation"),
        var_95=pf.get("_var95"),
        stress_10pct=(dims.get("stress_tests", {})
                      .get(-0.1, {}).get("pnl")),
        warnings=[b["rule"] for b in
                  pf.get("breaches", [])] if pf.get("breaches") else [])
    # external positions live INSIDE ctx as pseudo-positions now —
    # split nav by flag so sources never double-count
    intl_pos = [p for p in pf["positions"] if not p.get("external")]
    intl_pos_nav = sum(p["market_value"] for p in intl_pos)
    intl_cash = pf.get("cash_internal") or 0.0
    has_book = bool(pf.get("has_book"))
    if source == "internal":
        # internal book = ledger cash + position market values ONLY —
        # external account balances are a different book and must not
        # surface here (they're reachable via the source selector)
        has_positions = has_book
        nav = (intl_cash + intl_pos_nav) if has_book else None
        cash = intl_cash if has_book else None
        unreal_pnl = (sum(p.get("unrealized", 0) for p in intl_pos)
                      if intl_pos else None)
        daily_pnl = (sum(p["daily_pnl"] for p in intl_pos
                         if p.get("daily_pnl") is not None)
                     if any(p.get("daily_pnl") is not None
                            for p in intl_pos) else None)
    else:
        has_positions = bool(pf["positions"])

    # external sources (MT4 push, Bamboo sync, IBKR bridge…) — merge
    # per ?source=; "all" includes EVERY connected account
    if source != "internal":
        from app.models.portfolio import ExternalAccount
        from app.models.market import MarketQuote
        q = select(ExternalAccount).where(ExternalAccount.connected)
        if source != "all":
            q = q.where(ExternalAccount.source == source)
        ext = (await db.execute(q)).scalars().all()
        # FX-normalize to USD — accounts can be GBP/EUR denominated;
        # stored '{CCY}USD=X' quotes convert, else raw (documented)
        fxq = dict((await db.execute(
            select(MarketQuote.symbol, MarketQuote.mid)
            .where(MarketQuote.source == "yahoo"))).all())

        def _usd(v, ccy):
            if v is None:
                return 0.0
            v = float(v)
            if (ccy or "USD") == "USD":
                return v
            r = fxq.get(f"{ccy}USD=X")
            return v * float(r) if r else v

        ext_nav = sum(_usd(a.equity or a.balance, a.currency) for a in ext)
        ext_cash = sum(_usd(a.balance, a.currency) for a in ext)
        # external unrealized = equity − balance; daily = eq delta vs
        # first snapshot today (equity_history)
        ext_unreal = sum(_usd(float(a.equity or 0) - float(a.balance or 0),
                              a.currency)
                         for a in ext if a.equity is not None)
        today = datetime.now(UTC).date().isoformat()
        ext_daily = 0.0
        for a in ext:
            hist = a.equity_history or []
            past = [h for h in hist if h.get("t", "")[:10] < today]
            if past and a.equity is not None:
                ext_daily += _usd(float(a.equity)
                                  - float(past[-1]["equity"]), a.currency)
        if source == "all":
            # combined book — internal equity (cash + positions) once,
            # external broker equity once; `pf["cash"]` already folds
            # ext balances in, so adding ext_cash to it would count
            # them twice — build from cash_internal instead
            nav = ((intl_cash + intl_pos_nav) if has_book else 0) \
                + ext_nav
            nav = nav or None
            cash = ((intl_cash if has_book else 0) + ext_cash) or None
            intl_unreal = (sum(p.get("unrealized", 0)
                               for p in intl_pos) if intl_pos else None)
            unreal_pnl = ((intl_unreal or 0) + ext_unreal
                          if (intl_unreal or ext_unreal) else None)
            daily_pnl = ((pf.get("daily_pnl") or 0) + ext_daily
                         if (pf.get("daily_pnl") or ext_daily)
                         else None)
            has_positions = has_positions or bool(ext)
        else:
            nav, cash = ext_nav or None, ext_cash or None
            unreal_pnl = ext_unreal if ext else None
            daily_pnl = ext_daily if ext else None
            has_positions = bool(ext)
    dpct = (daily_pnl / (nav - daily_pnl) * 100
            if nav and daily_pnl else None)
    # per-position holdings — filtered by source, sorted by exposure
    show = intl_pos if source == "internal" else \
        [p for p in pf["positions"]
         if (not p.get("external")) or source == "all"
         or p.get("source") == source]
    holdings = sorted(
        [{"symbol": p.get("display_symbol") or p["symbol"],
          "market_value": p["market_value"],
          "unrealized": p.get("unrealized"),
          "weight": p["market_value"] / (nav or 1),
          "sector": p.get("sector"),
          "source": p.get("source", "ledger")}
         for p in show],
        key=lambda x: -x["market_value"])
    portfolio = PortfolioSummary(
        total_value=nav, cash=cash,
        daily_pnl=daily_pnl, daily_pnl_pct=dpct,
        unrealized_pnl=unreal_pnl, holdings=holdings)

    # alerts
    arows = (
        await db.execute(
            select(Alert).where(Alert.status == "active")
            .order_by(Alert.created_at.desc()).limit(10))
    ).scalars().all()
    alerts = [
        AlertItem(id=a.id, severity=a.severity,
                  message=a.message, source=a.source,
                  at=a.created_at.isoformat())
        for a in arows]

    # pending approvals + proposed order tickets
    pending = (
        await db.execute(
            select(Approval, DecisionRecord, Instrument.symbol)
            .join(DecisionRecord, Approval.decision_id == DecisionRecord.id)
            .join(Instrument,
                  DecisionRecord.instrument_id == Instrument.id)
            .where(Approval.status == "pending")
            .limit(10))
    ).all()
    approvals = [
        ApprovalItem(id=a.id, ticker=s,
                     action="review decision",
                     requested_at=a.created_at.isoformat(),
                     cio_rating=d.verdict,
                     risk_verdict=("block" if d.gate_results.get(
                         "tree", {}).get("overall") == "blocked"
                         else "pass"))
        for a, d, s in pending]

    # decisions
    drows = (
        await db.execute(
            select(DecisionRecord, Instrument.symbol)
            .join(Instrument,
                  DecisionRecord.instrument_id == Instrument.id)
            .order_by(DecisionRecord.at.desc()).limit(8))
    ).all()
    decisions = [
        DecisionItem(id=d.id, ticker=s, verdict=d.verdict or "?",
                     at=d.at.isoformat(),
                     cio_confidence=int(d.cio_confidence * 100)
                     if d.cio_confidence is not None else None)
        for d, s in drows]

    # open orders → signals
    ords = (
        await db.execute(
            select(BrokerOrderRec, Instrument.symbol)
            .join(Instrument,
                  BrokerOrderRec.instrument_id == Instrument.id)
            .where(BrokerOrderRec.status.not_in(
                ["filled", "rejected", "cancelled"]))
            .limit(10))
    ).all()
    signals = [
        TradeSignal(kind="order",
                    message=f"{s} {o.side} {o.qty} — {o.status}",
                    danger=o.status == "unknown")
        for o, s in ords]
    if len(signals) < 4:
        # persisted technical scan results — produced by the
        # `technical:scan` job (runs after each backfill, or on demand)
        try:
            from app.models.market import TechnicalScanResult
            rows = (await db.execute(
                select(TechnicalScanResult, Instrument.symbol)
                .join(Instrument,
                      TechnicalScanResult.instrument_id == Instrument.id)
                .where(TechnicalScanResult.decision.not_in(
                    ["wait", "no_trade", "invalid_data"]))
                .order_by(TechnicalScanResult.created_at.desc())
                .limit(5))).all()
            for r, sym_ in rows:
                signals.append(TradeSignal(
                    kind="technical",
                    message=f"{sym_}: {r.decision.replace('_', ' ')} "
                            f"({r.engine})",
                    danger=False))
        except Exception as e:
            import logging
            logging.warning("CC technical signals failed: %s", e)
            await db.rollback()

        # held-position exit checks — the book you actually own
        try:
            scan_map = {}
            try:
                from app.models.market import TechnicalScanResult
                from app.models.instruments import Instrument as _Inst
                for r, sym_ in (await db.execute(
                        select(TechnicalScanResult, _Inst.symbol)
                        .join(_Inst,
                              TechnicalScanResult.instrument_id
                              == _Inst.id))).all():
                    scan_map.setdefault(sym_, r)
            except Exception:
                await db.rollback()
            for hp in pf["positions"]:
                sym_ = hp["symbol"]
                mv = hp.get("market_value") or 0
                nav_ = pf["nav"] or 1
                # weight breach — every real holding, internal or ext
                if mv / nav_ > 0.10:
                    signals.append(TradeSignal(
                        kind="risk",
                        message=f"{sym_}: {mv/nav_*100:.0f}% of book"
                                f" — above 10% name limit",
                        danger=True))
                # deep drawdown on position
                unr = hp.get("unrealized")
                if unr is not None and mv:
                    if unr / mv < -0.08:
                        signals.append(TradeSignal(
                            kind="exit",
                            message=f"{sym_}: unrealized "
                                    f"{unr/mv*100:.0f}% — review "
                                    f"thesis / stop discipline",
                            danger=True))
                # above intrinsic value → trim candidate
                piv = hp.get("price_vs_iv")
                if piv is not None and piv > 0:
                    signals.append(TradeSignal(
                        kind="exit",
                        message=f"{sym_}: trading {piv*100:.0f}% above"
                                f" IV — trim candidate",
                        danger=False))
                # stale technical decision for held name
                sr = scan_map.get(sym_)
                if sr and sr.decision in ("wait",):
                    signals.append(TradeSignal(
                        kind="technical",
                        message=f"{sym_}: technical {sr.decision} "
                                f"(holding, no add signal)",
                        danger=False))
        except Exception as e:
            import logging
            logging.warning("CC position signals failed: %s", e)
            await db.rollback()

    # watchlist: top Green Zone scores from latest screening run
    from app.models.screening import ScreeningResult
    entries: list[WatchlistItem] = []
    best = (
        await db.execute(
            select(ScreeningResult, Instrument.symbol, Instrument.name)
            .join(Instrument,
                  ScreeningResult.instrument_id == Instrument.id)
            .order_by(ScreeningResult.score.desc()).limit(12))
    ).all()
    seen_syms: set[str] = set()
    watch_ids = []
    for sr, sym, nm in best:
        if sym in seen_syms:
            continue
        seen_syms.add(sym)
        watch_ids.append((sr, sym, nm))
        if len(watch_ids) >= 8:
            break
    # one batched query — latest daily close per watchlist instrument
    inst_ids = [sr.instrument_id for sr, _, _ in watch_ids]
    latest_close: dict[str, float] = {}
    if inst_ids:
        bars = (
            await db.execute(
                select(OhlcvBar.instrument_id, OhlcvBar.close)
                .where(OhlcvBar.instrument_id.in_(inst_ids),
                       OhlcvBar.timeframe == "1d")
                .order_by(OhlcvBar.time.desc()))
        ).all()
        for iid, close in bars:
            latest_close.setdefault(iid, float(close))
    entries = [WatchlistItem(
        ticker=sym, name=nm,
        green_zone_score=int(sr.score) if sr.score else None,
        last_price=latest_close.get(sr.instrument_id))
        for sr, sym, nm in watch_ids]

    # job health → provider rows
    job_count = (await db.execute(
        select(func.count(JobRun.id)))).scalar()
    last_runs = (
        await db.execute(
            select(Job.key, func.max(JobRun.finished_at),
                   func.max(JobRun.status))
            .join(Job, JobRun.job_id == Job.id)
            .group_by(Job.key))
    ).all() if job_count else []

    # CIO block — latest decision's verdict/confidence
    cio = CioBlock()
    if drows:
        d0, _ = drows[0]
        cio = CioBlock(
            rating=(d0.verdict or "").upper().replace("_", " "),
            confidence=int(d0.cio_confidence * 100)
            if d0.cio_confidence is not None else None,
            mos_pct=(d0.numbers or {}).get("mos"),
            risk_veto=(d0.gate_results.get("conflict") or {})
                .get("reason"),
            conflict_note=None,
            book_note=(f"real book: {len(pf['positions'])} positions · "
                       f"NAV ${nav:,.0f} · source {source}")
                if nav else None,
        )

    # split — mandate targets are real policy (not portfolio marks)
    from app.models.mandate import Mandate
    m = (
        await db.execute(
            select(Mandate).order_by(Mandate.version.desc()).limit(1))
    ).scalars().first()
    split = PortfolioSplit(
        investment_pct=m.investment_split_pct if m else 70,
        trading_pct=m.trading_split_pct if m else 30)

    # agents — latest output per agent (agent_runs → outputs join)
    agents: list[AgentVote] = []
    outs = (
        await db.execute(
            select(AgentOutput, AgentRun.agent_key)
            .join(AgentRun, AgentOutput.run_id == AgentRun.id)
            .order_by(AgentOutput.created_at.desc()).limit(40))
    ).all()
    seen_agents: set[str] = set()
    for o, key in outs:
        if key in seen_agents:
            continue
        seen_agents.add(key)
        agents.append(AgentVote(
            agent=key, recommendation=o.recommendation or "?",
            score=int(o.score) if o.score is not None else None))

    # sectors — prefer the 11 SPDR sector ETFs (real market sectors);
    # fall back to mean 1D/1W return of universe names grouped by
    # sector when the ETFs aren't ingested yet
    from app.services.market_context import CONTEXT_INSTRUMENTS, \
        SECTOR_ETF_MAP
    sectors: list[SectorPerf] = []
    try:
        etf_syms = list(SECTOR_ETF_MAP)
        etf_rows = (await db.execute(
            select(Instrument.id, Instrument.symbol)
            .where(Instrument.symbol.in_(etf_syms)))).all()
        if etf_rows:
            cutoff = datetime.now(UTC) - timedelta(days=10)
            etf_bars = (await db.execute(
                select(OhlcvBar.instrument_id, OhlcvBar.time,
                       OhlcvBar.close)
                .where(OhlcvBar.instrument_id.in_(
                       [r[0] for r in etf_rows]),
                       OhlcvBar.timeframe == "1d",
                       OhlcvBar.time >= cutoff)
                .order_by(OhlcvBar.time))).all()
            by_i: dict[str, list] = {}
            for iid, t, c in etf_bars:
                by_i.setdefault(iid, []).append((t, float(c)))
            prefs = (rg.sector_preferences
                     if rg and getattr(rg, "sector_preferences",
                                       None) else {})
            favored = set(prefs.keys())
            for iid, sym in etf_rows:
                pts = by_i.get(iid, [])
                if len(pts) < 2:
                    continue
                name = SECTOR_ETF_MAP[sym]
                last, first, prev = pts[-1][1], pts[0][1], pts[-2][1]
                w1 = (last - first) / first * 100
                sectors.append(SectorPerf(
                    sector=name + (" ★" if name in favored else ""),
                    weight_pct=None,
                    day_pct=round((last - prev) / prev * 100, 2),
                    week_pct=round(w1, 2),
                    momentum=min(100, int(abs(w1) * 10))))
            sectors.sort(key=lambda s: -(s.week_pct or 0))
    except Exception as e:
        import logging
        logging.warning("CC sector-etf perf failed: %s", e)
    if not sectors:
        try:
            sec_rows = (await db.execute(
                select(Instrument.id, Instrument.symbol, Sector.name)
                .outerjoin(Sector, Instrument.sector_id == Sector.id)
                .where(Instrument.is_active))).all()
            ids = [r[0] for r in sec_rows]
            if ids:
                cutoff = datetime.now(UTC) - timedelta(days=10)
                recent = (await db.execute(
                    select(OhlcvBar.instrument_id, OhlcvBar.time,
                           OhlcvBar.close)
                    .where(OhlcvBar.instrument_id.in_(ids),
                           OhlcvBar.timeframe == "1d",
                           OhlcvBar.time >= cutoff)
                    .order_by(OhlcvBar.time))).all()
                by_i: dict[str, list] = {}
                for iid, t, c in recent:
                    by_i.setdefault(iid, []).append((t, float(c)))
                agg: dict[str, dict] = {}
                for iid, sym, sname in sec_rows:
                    pts = by_i.get(iid, [])
                    if len(pts) < 2:
                        continue
                    sec = sname or "Unclassified"
                    d = agg.setdefault(sec, {"n": 0, "d1": [], "w1": []})
                    d["n"] += 1
                    last, first = pts[-1][1], pts[0][1]
                    prev = pts[-2][1]
                    d["d1"].append((last - prev) / prev * 100)
                    d["w1"].append((last - first) / first * 100)
                total = sum(d["n"] for d in agg.values()) or 1
                prefs = (rg.sector_preferences
                         if rg and getattr(rg, "sector_preferences",
                                           None) else {})
                favored = set(prefs.keys())
                sectors = [
                    SectorPerf(
                        sector=(name + (" ★" if name in favored else "")),
                        weight_pct=round(d["n"] / total * 100, 1),
                        day_pct=round(sum(d["d1"]) / len(d["d1"]), 2),
                        week_pct=round(sum(d["w1"]) / len(d["w1"]), 2),
                        momentum=min(100, int(abs(
                            sum(d["w1"]) / len(d["w1"])) * 10)))
                    for name, d in sorted(
                        agg.items(),
                        key=lambda kv: -abs(sum(kv[1]["w1"])
                                            / len(kv[1]["w1"])))
                ][:10]
        except Exception as e:
            import logging
            logging.warning("CC sectors failed: %s", e)
    if not sectors:
        pref_score = {"favored": 80.0, "neutral": 50.0, "avoid": 20.0}
        sectors = [SectorPerf(sector=s,
                              momentum=pref_score.get(str(w), 50.0))
                   for s, w in (rg.sector_preferences.items() if rg
                                else [])] if rg else []

    # macro calendar — economic_releases populated by
    # ingest:fred:calendar (rides on backfill)
    from app.models.market import EconomicRelease
    rels = (await db.execute(
        select(EconomicRelease)
        .where(EconomicRelease.release_at >=
               datetime.now(UTC) - timedelta(days=1))
        .order_by(EconomicRelease.release_at).limit(8))
    ).scalars().all()
    calendar = [CalendarEvent(title=r.title,
                              at=r.release_at.isoformat(),
                              detail=r.source) for r in rels]

    # market context strip — indices, vol, rates, macro ETFs; the
    # tape the desk is actually watching
    market_strip: list[dict] = []
    ctx_syms = list(CONTEXT_INSTRUMENTS)
    ctx_rows = (await db.execute(
        select(Instrument.id, Instrument.symbol, Instrument.name,
               Instrument.asset_class)
        .where(Instrument.symbol.in_(ctx_syms)))).all()
    if ctx_rows:
        c_bars = (await db.execute(
            select(OhlcvBar.instrument_id, OhlcvBar.time,
                   OhlcvBar.close)
            .where(OhlcvBar.instrument_id.in_(
                   [r[0] for r in ctx_rows]),
                   OhlcvBar.timeframe == "1d",
                   OhlcvBar.time >=
                   datetime.now(UTC) - timedelta(days=10))
            .order_by(OhlcvBar.time))).all()
        by_i2: dict[str, list] = {}
        for iid, t, cl in c_bars:
            by_i2.setdefault(iid, []).append((t, float(cl)))
        grp = {s: CONTEXT_INSTRUMENTS[s][2] for s in ctx_syms}
        for iid, sym, nm, _acls in ctx_rows:
            pts = by_i2.get(iid, [])
            if not pts:
                continue
            last, first = pts[-1][1], pts[0][1]
            prev = pts[-2][1] if len(pts) > 1 else first
            market_strip.append({
                "symbol": sym, "name": nm, "group": grp.get(sym, ""),
                "close": last,
                "day_pct": round((last - prev) / prev * 100, 2)
                if prev else None,
                "week_pct": round((last - first) / first * 100, 2)
                if first else None,
                "as_of": pts[-1][0].isoformat()[:10]})
        grp_rank = {"index": 0, "volatility": 1, "rates": 2,
                    "macro": 3, "sector": 4}
        market_strip.sort(
            key=lambda x: (grp_rank.get(x["group"], 9), x["symbol"]))

    # Layer-IV sleeve (doc Phase-0/Step-2) — computed inside
    # _portfolio_ctx alongside the risk context so the dashboard shows
    # exactly the caps the order gate enforces
    sleeve_ctx = pf.get("sleeve") or {}
    sleeve = None
    if sleeve_ctx.get("enabled"):
        st = sleeve_ctx.get("state") or {}
        sleeve = {
            "sleeve_equity": st.get("sleeve_equity"),
            "gross": st.get("gross"),
            "effective_gross_cap": st.get("effective_gross_cap"),
            "gross_cap": st.get("gross_cap"),
            "used_margin": st.get("used_margin"),
            "free_margin": st.get("free_margin"),
            "margin_utilisation": st.get("margin_utilisation"),
            "effective_leverage": st.get("effective_leverage"),
            "maint_margin_used": st.get("maint_margin_used"),
            "buffer_capped": st.get("buffer_capped"),
            "max_asset_notional": st.get("max_asset_notional"),
            "starter_notional": st.get("starter_notional"),
            "portfolio_stop_usd": st.get("portfolio_stop_usd"),
            "per_trade_risk_budget": st.get("per_trade_risk_budget"),
            "open_positions": st.get("positions_used"),
            "max_positions": st.get("max_positions"),
            "unrealized_pnl": st.get("unrealized_pnl"),
            "realized_pnl": st.get("realized_pnl"),
            "drawdown_pct": st.get("drawdown_pct"),
            "drawdown_usd": st.get("drawdown_usd"),
            "stop_floor_usd": sleeve_ctx.get("stop_floor_usd"),
            "stop_floor_binding": sleeve_ctx.get("stop_floor_binding"),
            "distance_to_portfolio_stop_usd": sleeve_ctx.get(
                "distance_to_portfolio_stop_usd"),
            "cooldown": sleeve_ctx.get("cooldown"),
            "lifecycle": sleeve_ctx.get("lifecycle"),
            "positions": [
                {"symbol": p["symbol"], "state": p["state"],
                 "market_value": p["market_value"],
                 "unrealized": p.get("unrealized"),
                 "open_risk": p.get("open_risk"),
                 "stop": p.get("stop"), "target": p.get("target"),
                 "current_price": p.get("current_price")}
                for p in (sleeve_ctx.get("positions") or [])],
        }

    resp = CommandCenterResponse(
        generated_at=datetime.now(UTC).isoformat(),
        portfolio=portfolio,
        split=split,
        regime=regime,
        risk=risk,
        sleeve=sleeve,
        cio=cio,
        agents=agents,
        sectors=sectors,
        calendar=calendar,
        market=market_strip,
        alerts=alerts,
        approvals=approvals,
        decisions=decisions,
        signals=signals,
        watchlist=entries,
        providers=providers + [
            ProviderHealth(
                name=jid, status=("up" if st == "success"
                                  else "degraded"),
                last_sync=ft.isoformat() if ft else None)
            for jid, ft, st in last_runs],
    )

    # demo fill — only sections with no real data get fixtures, and
    # are explicitly labeled in demo_sections
    if demo_on:
        fill = {
            "portfolio": (resp.portfolio.total_value is None,
                          demo.DEMO_PORTFOLIO),
            "regime": (resp.regime.economic_regime is None,
                       demo.DEMO_REGIME),
            "risk": (resp.risk.max_sector_pct is None,
                     demo.DEMO_RISK),
            "cio": (resp.cio.rating is None, demo.DEMO_CIO),
            "signals": (not resp.signals, demo.DEMO_SIGNALS),
            "watchlist": (not resp.watchlist, demo.DEMO_WATCHLIST),
            "approvals": (not resp.approvals, demo.DEMO_APPROVALS),
            "alerts": (not resp.alerts, demo.DEMO_ALERTS),
            "decisions": (not resp.decisions, demo.DEMO_DECISIONS),
        }
        # split/agents/sectors are always real now (mandate, agent runs,
        # regime rotation prefs); calendar is intentionally absent — no
        # provider exists yet, so no fixture fills it.
        for name, (empty, fixture) in fill.items():
            if empty:
                setattr(resp, name, fixture)
                resp.demo_sections.append(name)
    cache.put(f"command_center:{source}:{demo_on}", resp)
    return resp
