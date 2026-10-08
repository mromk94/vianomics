from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.ops import Job, JobRun, QuarantinedRecord, SyncStatus
from app.models.providers import DataProvider

router = APIRouter(prefix="/dataops", tags=["dataops"])


@router.get("/overview")
async def overview(db: AsyncSession = Depends(get_db)):
    """Jobs + last run + sync freshness + quarantine counts."""
    jobs = (await db.execute(select(Job))).scalars().all()
    last_runs = {
        jid: (ft, st)
        for jid, ft, st in (
            await db.execute(
                select(JobRun.job_id, func.max(JobRun.finished_at),
                       func.max(JobRun.status))
                .group_by(JobRun.job_id))).all()}
    providers = {
        p.id: p.name
        for p in (await db.execute(select(DataProvider))).scalars()}
    sync_rows = (await db.execute(select(SyncStatus))).scalars().all()
    quarantine = dict(
        (await db.execute(
            select(QuarantinedRecord.target_table,
                   func.count(QuarantinedRecord.id))
            .group_by(QuarantinedRecord.target_table))).all())

    return {
        "jobs": [
            {"id": j.id, "key": j.key, "kind": j.kind,
             "schedule": j.schedule, "enabled": j.enabled,
             "provider": providers.get(j.provider_id),
             "last_run_at": (last_runs[j.id][0].isoformat()
                             if last_runs.get(j.id, (None,))[0]
                             else None),
             "last_status": last_runs.get(j.id, (None, None))[1]}
            for j in jobs],
        "sync": [
            {"provider": providers.get(s.provider_id, "?"),
             "dataset": s.dataset,
             "last_success": s.last_success_at.isoformat()
             if s.last_success_at else None,
             "last_error": s.last_error,
             "lag_seconds": s.lag_seconds}
            for s in sync_rows],
        "quarantine": [
            {"target_table": k, "count": v}
            for k, v in quarantine.items()],
    }


@router.get("/quarantine")
async def quarantined(limit: int = 50,
                      db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(QuarantinedRecord)
            .order_by(QuarantinedRecord.created_at.desc()).limit(limit))
    ).scalars().all()
    return [
        {"id": r.id, "target_table": r.target_table,
         "errors": r.errors, "raw": r.raw, "status": r.status,
         "at": r.created_at.isoformat()}
        for r in rows]


@router.get("/runs/{job_key}")
async def runs_for(job_key: str, limit: int = 20,
                   db: AsyncSession = Depends(get_db)):
    job = (
        await db.execute(select(Job).where(Job.key == job_key))
    ).scalar_one_or_none()
    if job is None:
        return []
    rows = (
        await db.execute(
            select(JobRun).where(JobRun.job_id == job.id)
            .order_by(JobRun.started_at.desc()).limit(limit))
    ).scalars().all()
    return [
        {"id": r.id, "status": r.status,
         "started": r.started_at.isoformat(),
         "finished": r.finished_at.isoformat()
         if r.finished_at else None,
         "in": r.records_in, "ok": r.records_ok,
         "quarantined": r.records_quarantined,
         "error": r.error}
        for r in rows]


# ── machine-to-machine scheduler trigger ──
# External cron (Render cronjobs, GitHub Actions, cron-of-any-kind)
# calls POST /api/v1/internal/jobs/{key} with X-Cron-Secret. The path
# is whitelisted from session auth (middleware PUBLIC_PREFIXES) so a
# scheduler never needs a human session token; the secret is enforced
# HERE — unset CRON_SECRET means every call fails closed (503).
internal_router = APIRouter(prefix="/internal", tags=["internal"])


@internal_router.post("/jobs/{job_key:path}", status_code=202)
async def cron_trigger(job_key: str,
                       request: Request,
                       db: AsyncSession = Depends(get_db)):
    import hmac

    from app.config import get_settings
    secret = get_settings().cron_secret
    if not secret:
        raise HTTPException(503, "CRON_SECRET not configured — "
                               "internal job endpoint disabled")
    sent = request.headers.get("x-cron-secret", "")
    if not hmac.compare_digest(sent, secret):
        raise HTTPException(401, "invalid cron secret")
    return await run_job_now(job_key, db)


@router.post("/run/{job_key}", status_code=202)
async def run_job_now(job_key: str,
                      db: AsyncSession = Depends(get_db)):
    """Manually trigger an ingestion job by its key —
    ingest:edgar:facts:AAPL, ingest:fred:CPIAUCSL, ingest:yahoo:MSFT."""
    from app.ingestion import jobs as ing
    from app.providers.edgar import EdgarAdapter
    from app.providers.fred import FredAdapter
    from app.providers.yahoo import YahooAdapter
    adapters = {"edgar": EdgarAdapter, "fred": FredAdapter,
                "yahoo": YahooAdapter}
    parts = job_key.split(":")
    try:
        if job_key in ("technical:scan", "monitor:scan", "pyramid:seed",
                       "pyramid:maintain", "review:weekly",
                       "review:monthly", "review:quarterly"):
            from app.ingestion.upsert import get_or_create
            from app.models.ops import Job, JobRun
            from app.db.base import utcnow
            job, _ = await get_or_create(
                db, Job, {"key": job_key},
                {"kind": "analysis"})
            run = JobRun(job_id=job.id)
            db.add(run)
            await db.flush()
            try:
                if job_key == "technical:scan":
                    from app.services import technical_engine as te
                    n = await te.run_scan(db)
                elif job_key == "pyramid:seed":
                    from app.services import pyramid_seed
                    res = await pyramid_seed.seed_candidates(db)
                    n = res["created"]
                elif job_key == "pyramid:maintain":
                    from app.services import pyramid_maintain
                    res = await pyramid_maintain.maintain_open_pyramids(
                        db)
                    n = res["processed"]
                elif job_key.startswith("review:"):
                    # Phase-6 cadence — weekly Four-M's, monthly MOS
                    # re-rank, quarterly margin review
                    from app.services import review_jobs
                    res = await review_jobs.REVIEW_JOBS[job_key](db)
                    n = (res.get("reviewed") or res.get("repriced")
                         or 1)
                else:
                    from app.services import monitoring as mon
                    res = await mon.run_checks(db)
                    n = sum(res.get("emitted", {}).values())
                run.status, run.records_ok, run.finished_at = (
                    "success", n, utcnow())
            except Exception as e:
                run.status, run.error, run.finished_at = (
                    "failed", str(e)[:200], utcnow())
            db.add(run)
            await db.commit()
        elif job_key == "pipeline:universe":
            # long-running (universe × ~35s) — fire-and-forget like
            # backfill; progress visible via decision records
            import asyncio
            from app.db.session import SessionFactory
            from app.services import pipeline as pl

            async def _bg():
                async with SessionFactory() as s:
                    try:
                        await pl.run_pipeline(s)
                        await s.commit()
                    except Exception:
                        await s.rollback()
            task = asyncio.create_task(_bg())
            _BG_TASKS.add(task)
            task.add_done_callback(_BG_TASKS.discard)
            return {"status": "started",
                    "note": "universe pipeline running in background"}
        elif job_key in ("market:context", "market:quotes"):
            # context seed (indices/sector ETFs) + quote refresh
            from app.services import market_context as mc
            n1 = await mc.ensure_market_context(db)
            n2 = await mc.refresh_quotes(db) if job_key == \
                "market:quotes" else 0
            await db.commit()
            return {"status": "success",
                    "records_ok": n1 + n2}
        elif job_key == "ingest:fred:calendar":
            from app.services.secrets import get_secret
            import os
            key = (await get_secret(db, "FRED_API_KEY")
                   or os.environ.get("FRED_API_KEY"))
            run = await ing.ingest_fred_calendar(
                db, adapters["fred"](api_key=key))
            if run is None:
                return {"status": "skipped",
                        "error": "no FRED_API_KEY configured"}
        elif job_key.startswith("ingest:edgar:facts:"):
            sym = parts[-1]
            if await _instr(db, sym) is None:
                return {"status": "failed", "error": f"{sym} not in universe"}
            run = await ing.ingest_edgar_facts(db, adapters["edgar"](), sym)
        elif job_key.startswith("ingest:yahoo:fundamentals:"):
            sym = parts[-1]
            if await _instr(db, sym) is None:
                return {"status": "failed",
                        "error": f"{sym} not in universe"}
            run = await ing.ingest_yahoo_fundamentals(
                db, adapters["yahoo"](), sym)
        elif job_key == "fundamentals:gapfill":
            # coverage sweep — tracked instruments with missing/stale
            # facts re-ingest (EDGAR → Yahoo fallback), bounded batch
            from app.services import universe as usvc
            res = await usvc.fundamentals_gapfill(db, limit=40)
            await db.commit()
            return res
        elif job_key.startswith("ingest:fred:"):
            from app.services.secrets import get_secret
            import os
            key = (await get_secret(db, "FRED_API_KEY")
                   or os.environ.get("FRED_API_KEY"))
            run = await ing.ingest_fred_series(
                db, adapters["fred"](api_key=key), parts[-1], parts[-1],
                use_csv=not key)
        elif job_key == "market:alpaca:quotes":
            # snapshot sweep → market_quotes (source='alpaca')
            from app.services import market_context as mc
            n = await mc.refresh_alpaca_quotes(db)
            await db.commit()
            return {"status": "success", "records_ok": len(n)}
        elif job_key == "portfolio:alpaca:sync":
            from app.services import broker_sync
            res = await broker_sync.sync_alpaca_account(db)
            await db.commit()
            return res
        elif job_key == "universe:alpaca:sync":
            # full US-equity discovery pool → 'global' universe
            from app.services import universe as usvc
            res = await usvc.sync_alpaca_universe(db)
            await db.commit()
            return res
        elif job_key == "portfolio:ibkr:sync":
            from app.services import broker_sync
            # bridge first — live socket beats the 24h-batch Flex
            # statement; Flex remains the no-daemon fallback
            res = await broker_sync.sync_ibkr_bridge(db)
            if res.get("status") == "skipped":
                res = await broker_sync.sync_ibkr_flex(db)
            await db.commit()
            return res
        elif job_key.startswith("ingest:alpaca:"):
            # ingest:alpaca:<sym>[:<timeframe>]  e.g. ingest:alpaca:NVDA
            from app.providers.alpaca import AlpacaAdapter
            from app.providers.base import ProviderConfigError
            try:
                sym = parts[2]
                tf = parts[3] if len(parts) > 3 else "1Day"
                if await _instr(db, sym) is None:
                    return {"status": "failed",
                            "error": f"{sym} not in universe"}
                run = await ing.ingest_alpaca_bars(
                    db, AlpacaAdapter(), sym, timeframe=tf)
            except ProviderConfigError as e:
                return {"status": "failed", "error": str(e)}
        elif job_key == "market:intraday":
            # IEX intraday bars for every context instrument
            from app.services.secrets import get_secret
            import os
            key = (await get_secret(db, "TIINGO_API_KEY")
                   or os.environ.get("TIINGO_API_KEY"))
            if not key:
                return {"status": "failed",
                        "error": "TIINGO_API_KEY not configured"}
            from app.providers.market import TiingoAdapter
            from app.services.market_context import CONTEXT_INSTRUMENTS
            from app.ingestion.upsert import get_or_create
            from app.models.ops import Job, JobRun
            from app.db.base import utcnow
            adapter = TiingoAdapter(api_key=key)
            ok = 0
            for sym in CONTEXT_INSTRUMENTS:
                if sym.startswith("^"):
                    continue  # IEX covers stocks/ETFs, not indices
                r = await ing.ingest_tiingo_intraday(
                    db, adapter, symbol=sym, freq="30min")
                if r.status == "success":
                    ok += 1
                await db.commit()
            job, _ = await get_or_create(
                db, Job, {"key": job_key}, {"kind": "ingestion"})
            run = JobRun(job_id=job.id, status="success",
                         finished_at=utcnow(), records_ok=ok)
            db.add(run)
            await db.commit()
        elif job_key.startswith("ingest:tiingo:intraday:"):
            # ingest:tiingo:intraday:<sym>[:<freq>]
            from app.services.secrets import get_secret
            import os
            key = (await get_secret(db, "TIINGO_API_KEY")
                   or os.environ.get("TIINGO_API_KEY"))
            if not key:
                return {"status": "failed",
                        "error": "TIINGO_API_KEY not configured"}
            from app.providers.market import TiingoAdapter
            sym = parts[3] if len(parts) > 3 else ""
            freq = parts[4] if len(parts) > 4 else "30min"
            run = await ing.ingest_tiingo_intraday(
                db, TiingoAdapter(api_key=key), symbol=sym, freq=freq)
        elif job_key.startswith("ingest:tiingo:"):
            from app.services.secrets import get_secret
            import os
            key = (await get_secret(db, "TIINGO_API_KEY")
                   or os.environ.get("TIINGO_API_KEY"))
            if not key:
                return {"status": "failed",
                        "error": "TIINGO_API_KEY not configured"}
            from app.providers.market import TiingoAdapter
            run = await ing.ingest_tiingo_bars(
                db, TiingoAdapter(api_key=key), parts[-1])
        elif job_key == "ingest:daily":
            # lean daily refresh — bars + market context/quotes only
            # (the scheduled post-close pull; /backfill is the full
            # pipeline incl. EDGAR/FRED/scans)
            from app.ingestion.upsert import get_or_create
            from app.models.ops import Job, JobRun
            from app.db.base import utcnow
            res = await refresh_daily_bars(db)
            job, _ = await get_or_create(
                db, Job, {"key": job_key}, {"kind": "ingestion"})
            run = JobRun(job_id=job.id, status="success",
                         finished_at=utcnow(),
                         records_ok=res["instruments"])
            db.add(run)
            await db.commit()
        elif job_key.startswith("ingest:yahoo:") or \
                job_key.startswith("ingest:stooq:"):
            run = await ing.ingest_stooq_bars(db, adapters["yahoo"](), parts[-1])
        else:
            return {"status": "failed",
                    "error": f"no runner for {job_key}"}
    except Exception as e:
        return {"status": "failed", "error": str(e)[:200]}
    from app.services import cache
    cache.invalidate()           # new bars change scan/snapshot
    return {"status": run.status, "records_ok": run.records_ok,
            "error": run.error}


async def _instr(db: AsyncSession, symbol: str):
    from app.models.instruments import Instrument
    return (await db.execute(
        select(Instrument).where(Instrument.symbol == symbol.upper()))
    ).scalar_one_or_none()


async def refresh_daily_bars(db: AsyncSession) -> dict:
    """Post-close daily refresh: latest yahoo bars for every
    instrument + market context (indices/sector ETFs) + derived
    ADV + live quotes. Scheduled by the vaiip-ingest-daily cronjob so
    the symbol page and the maintenance loop never run on yesterday's
    book."""
    from app.db.base import utcnow
    from app.ingestion import jobs as ing
    from app.models.instruments import Instrument
    from app.models.market import OhlcvBar
    from app.models.universe import Universe, UniverseMembership
    from app.providers.yahoo import YahooAdapter
    from app.services import market_context as mc
    from sqlalchemy import or_

    await mc.ensure_market_context(db)
    await db.commit()
    ya = YahooAdapter()
    ok = 0
    # don't pull daily bars for the 10k-name global discovery pool —
    # only names the desk actually tracks (have bars already, or hold
    # active membership in a tiered universe). Global-only symbols
    # hydrate on view via the bars endpoint's self-heal.
    covered = select(OhlcvBar.instrument_id).distinct()
    tiered = (select(UniverseMembership.instrument_id)
              .join(Universe,
                    UniverseMembership.universe_id == Universe.id)
              .where(Universe.name != "global",
                     UniverseMembership.status == "active"))
    tracked = (await db.execute(
        select(Instrument).where(
            or_(Instrument.id.in_(covered),
                Instrument.id.in_(tiered))))).scalars().all()
    # prefer the paid Alpaca tape for daily bars when keys exist —
    # SIP/IEX is the authoritative feed; yahoo is the fallback, never
    # the only option while alpaca is configured
    from app.providers.alpaca import AlpacaAdapter
    from app.providers.base import ProviderConfigError
    try:
        alp = AlpacaAdapter()
    except ProviderConfigError:
        alp = None
    for inst in tracked:
        try:
            if alp is not None:
                # nightly refresh only needs recent sessions — dedupe
                # covers overlap; deep history is /backfill's job
                run = await ing.ingest_alpaca_bars(
                    db, alp, inst.symbol,
                    start=(utcnow() - timedelta(days=45))
                    .date().isoformat())
                if run.status != "success":
                    raise RuntimeError(run.error or "alpaca bars")
            else:
                await ing.ingest_stooq_bars(db, ya, inst.symbol)
            await db.commit()
            ok += 1
        except Exception:
            await db.rollback()
            # alpaca failure → yahoo keeps the book current
            try:
                await ing.ingest_stooq_bars(db, ya, inst.symbol)
                await db.commit()
                ok += 1
            except Exception:
                await db.rollback()
    try:
        await mc.update_adv(db)
        # quotes: alpaca snapshots for tracked names it covered;
        # yahoo for the uncovered + the whole context set
        t_syms = [i.symbol for i in tracked]
        covered_q = set(await mc.refresh_alpaca_quotes(db, t_syms))
        rest = [s for s in t_syms if s not in covered_q]
        if rest:
            await mc.refresh_quotes(db, rest)
        await mc.refresh_quotes(db)
        await db.commit()
    except Exception:
        await db.rollback()
    # fundamentals coverage sweep — a small nightly batch re-ingests
    # tracked names whose facts are missing or stale, so foreign
    # issuers and newly-tracked symbols stop screening "no data"
    gapfill = None
    try:
        from app.services import universe as usvc
        gapfill = await usvc.fundamentals_gapfill(db, limit=15)
        await db.commit()
    except Exception:
        await db.rollback()
    return {"instruments": ok,
            "bars_source": "alpaca" if alp is not None else "yahoo",
            "gapfill": gapfill}


_BG_TASKS: set = set()


@router.post("/backfill", status_code=202)
async def backfill():
    """Fire-and-forget: pull yahoo bars + FRED macro + EDGAR
    fundamentals for the whole universe in a background task —
    Render kills HTTP requests >~100s, so this must not block."""
    import asyncio
    task = asyncio.create_task(_backfill_all())
    _BG_TASKS.add(task)          # GC-safe: asyncio holds only weak refs
    task.add_done_callback(_BG_TASKS.discard)
    return {"started": True,
            "note": "running in background — check "
                    "/api/v1/dataops/overview for job runs"}


async def _backfill_all():
    import os
    from app.db.session import SessionFactory
    from app.ingestion import jobs as ing
    from app.models.instruments import Instrument
    from app.providers.yahoo import YahooAdapter
    from app.services import cache

    async with SessionFactory() as db:
        # market context first — indices/sector/macro ETFs power the
        # regime engine, sector rotation and Market Intelligence
        try:
            from app.services import market_context as mc
            await mc.ensure_market_context(db)
            await db.commit()
        except Exception:
            await db.rollback()
        insts = (await db.execute(select(Instrument))).scalars().all()
        ya = YahooAdapter()
        for inst in insts:
            try:
                await ing.ingest_stooq_bars(db, ya, inst.symbol)
                await db.commit()
            except Exception:
                await db.rollback()
        # fundamentals only for names that are actually tracked —
        # pulling SEC companyfacts for the 10k-name global discovery
        # pool would take days; pool members hydrate lazily on screen
        from app.models.market import OhlcvBar
        from app.models.universe import Universe, UniverseMembership
        covered_ids = select(OhlcvBar.instrument_id).distinct()
        tiered_ids = (select(UniverseMembership.instrument_id)
                      .join(Universe,
                            UniverseMembership.universe_id == Universe.id)
                      .where(Universe.name != "global",
                             UniverseMembership.status == "active"))
        fact_insts = [i for i in insts
                      if i.asset_class == "equity"]
        # cheap coverage check: tracked = has bars or tiered membership
        bar_covered = {r for (r,) in (await db.execute(
            covered_ids)).all()}
        tier_covered = {r for (r,) in (await db.execute(
            tiered_ids)).all()}
        tracked = [i for i in fact_insts
                   if i.id in bar_covered or i.id in tier_covered]
        # per-series isolation — one bad series never kills the rest
        # of the macro calendar (the 9/28 partial-ingest failure mode)
        try:
            from app.providers.fred import FredAdapter
            from app.services.macro_regime import FRED_SERIES
            from app.services.secrets import get_secret
            fred_key = (await get_secret(db, "FRED_API_KEY")
                        or os.environ.get("FRED_API_KEY"))
            fred = FredAdapter(api_key=fred_key)
            for code, (name, _cat) in FRED_SERIES.items():
                try:
                    await ing.ingest_fred_series(
                        db, fred, code, name, use_csv=True)
                    await db.commit()
                except Exception:
                    await db.rollback()
            # macro release calendar — FRED key when configured
            try:
                from app.ingestion.jobs import ingest_fred_calendar
                await ingest_fred_calendar(db, fred)
                await db.commit()
            except Exception:
                await db.rollback()
        except Exception:
            pass
        # EDGAR is public — a polite UA is baked into HttpAdapter.
        # Tracked names only (bars-covered or tiered); Yahoo fills the
        # issuers SEC doesn't carry. Pool members hydrate lazily.
        from app.providers.edgar import EdgarAdapter
        ed = EdgarAdapter()
        for inst in tracked:
            try:
                frun = await ing.ingest_edgar_facts(db, ed, inst.symbol)
                await db.commit()
                if not (frun.status == "success" and frun.records_ok):
                    raise RuntimeError("no edgar facts")
            except Exception:
                await db.rollback()
                try:
                    await ing.ingest_yahoo_fundamentals(
                        db, ya, inst.symbol)
                    await db.commit()
                except Exception:
                    await db.rollback()
        # derived state: ADV, live quotes for context, regime snapshot
        try:
            from app.services import market_context as mc
            await mc.update_adv(db)
            await mc.refresh_quotes(db)
            await db.commit()
        except Exception:
            await db.rollback()
        try:
            from datetime import UTC, datetime
            from app.services import macro_regime as mr
            from app.providers.market import fetch_fear_greed
            await mr.run_and_persist(db, datetime.now(UTC),
                                     fetch_fg=fetch_fear_greed)
            await db.commit()
        except Exception:
            await db.rollback()
        try:
            from app.services import technical_engine as te
            await te.run_scan(db)
        except Exception:
            await db.rollback()
        try:
            from app.services import pyramid_seed
            await pyramid_seed.seed_candidates(db)
        except Exception:
            await db.rollback()
        cache.invalidate()
        # monitoring sweep rides on every backfill — flags dead feeds
        try:
            from app.services import monitoring as mon
            await mon.run_checks(db)
            await db.commit()
        except Exception:
            await db.rollback()
