from datetime import UTC, datetime

from fastapi import APIRouter, Depends
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
async def command_center(db: AsyncSession = Depends(get_db)) -> CommandCenterResponse:
    settings = get_settings()
    from app.routers.settings import runtime_flag
    from app.services import cache
    demo_on = await runtime_flag(db, "demo_fixtures",
                                 settings.demo_fixtures)
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
    from app.models.ops import Alert, JobRun
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
                "as_of": rg.as_of.isoformat() if rg.as_of else ""})

    # portfolio / risk
    pf = await _portfolio_ctx(db)
    dims = re_.risk_dimensions(pf)
    conc = dims.get("concentration", {})
    risk = RiskUtilization(
        drawdown_pct=pf.get("max_dd"),
        max_sector_pct=conc.get("max_sector_pct"),
        max_position_pct=conc.get("max_single_name_pct"),
        avg_correlation=pf.get("avg_correlation"),
        stress_10pct=(dims.get("stress_tests", {})
                      .get(-0.1, {}).get("pnl")),
        warnings=[b["rule"] for b in
                  pf.get("breaches", [])] if pf.get("breaches") else [])
    # empty book → null (never present a placeholder as live NAV)
    has_positions = bool(pf.get("positions"))
    portfolio = PortfolioSummary(
        total_value=pf.get("nav") if has_positions else None,
        cash=pf.get("cash") if has_positions else None)

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
            select(JobRun.job_id, func.max(JobRun.finished_at),
                   func.max(JobRun.status)).group_by(JobRun.job_id))
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

    # sectors — real rotation prefs from the latest regime run
    # ('favored'/'neutral'/'avoid' → momentum score for display)
    pref_score = {"favored": 80.0, "neutral": 50.0, "avoid": 20.0}
    sectors = [SectorPerf(sector=s,
                          momentum=pref_score.get(str(w), 50.0))
               for s, w in (rg.sector_preferences.items() if rg
                            else [])] if rg else []

    resp = CommandCenterResponse(
        generated_at=datetime.now(UTC).isoformat(),
        portfolio=portfolio,
        split=split,
        regime=regime,
        risk=risk,
        cio=cio,
        agents=agents,
        sectors=sectors,
        alerts=alerts,
        approvals=approvals,
        decisions=decisions,
        signals=signals,
        watchlist=entries,
        providers=providers + [
            ProviderHealth(
                name=f"job:{jid}", status=("up" if st == "success"
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
    return resp
