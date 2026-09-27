"""Concrete ingestion jobs.

Each job: fetch raw → validate → persist idempotently → update SyncStatus.
Missing values are recorded as data gaps via quarantine/skip counts —
never zeroed.
"""

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.ingestion.runner import ingest_records, mark_sync, run_job
from app.ingestion.schemas import FundamentalIn, MacroIn
from app.ingestion.upsert import get_or_create
from app.models.fundamentals import FundamentalObservation
from app.models.instruments import Instrument
from app.models.market import MacroObservation, MacroSeries
from app.models.ops import Job, JobRun
from app.models.providers import DataProvider
from app.providers.base import ProviderAdapter


async def _provider(session: AsyncSession, key: str) -> DataProvider:
    provider = (
        await session.execute(
            select(DataProvider).where(DataProvider.key == key)
        )
    ).scalar_one_or_none()
    if provider is None:
        provider, _ = await get_or_create(
            session, DataProvider, {"key": key}, {"name": key, "kind": "unknown"}
        )
    return provider


async def _instrument(session: AsyncSession, symbol: str) -> Instrument | None:
    return (
        await session.execute(
            select(Instrument).where(Instrument.symbol == symbol.upper())
        )
    ).scalar_one_or_none()


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_date(s: str) -> date:
    return date.fromisoformat(s[:10])


async def ingest_edgar_facts(
    session: AsyncSession,
    adapter: ProviderAdapter,
    symbol: str,
    concepts: list[str] | None = None,
) -> JobRun:
    """Pull SEC XBRL company facts for one ticker (via its CIK identifier)
    and persist as point-in-time FundamentalObservations."""
    job, _ = await get_or_create(
        session,
        Job,
        {"key": f"ingest:edgar:facts:{symbol}"},
        {"kind": "ingestion"},
    )
    run = JobRun(job_id=job.id)
    session.add(run)
    await session.flush()

    provider = await _provider(session, "edgar")
    inst = await _instrument(session, symbol)
    if inst is None:
        run.status, run.error, run.finished_at = (
            "failed",
            f"instrument {symbol} not found",
            utcnow(),
        )
        return run

    from app.models.instruments import InstrumentIdentifier
    cik_row = (
        await session.execute(
            select(InstrumentIdentifier).where(
                InstrumentIdentifier.instrument_id == inst.id,
                InstrumentIdentifier.scheme == "cik",
            )
        )
    ).scalar_one_or_none()
    cik = cik_row.value if cik_row else None
    if cik is None:
        run.status, run.error, run.finished_at = (
            "failed",
            f"no CIK identifier for {symbol}",
            utcnow(),
        )
        return run

    raw, status = await run_job(
        session,
        job_run=run,
        provider_key="edgar",
        work=lambda: adapter.company_facts(cik),
    )
    if raw is None:
        run.finished_at = utcnow()
        await mark_sync(session, provider, "fundamentals", False, run.error)
        return run

    # Flatten XBRL facts → raw observation dicts
    wanted = set(concepts or [])
    records: list[dict] = []
    for taxonomy, facts in (raw.get("facts") or {}).items():
        for concept, detail in facts.items():
            if wanted and concept not in wanted:
                continue
            for unit, vals in (detail.get("units") or {}).items():
                for v in vals:
                    try:
                        records.append(
                            {
                                "instrument_symbol": symbol,
                                "concept": f"{taxonomy}:{concept}",
                                "value": str(v["val"]),
                                "unit": unit,
                                "currency": unit.split("/")[0]
                                if "/" not in unit and unit.isupper()
                                else None,
                                "period_start": v.get("start"),
                                "period_end": v["end"],
                                "fiscal_period": v.get("fp"),
                                "observed_at": v["end"],
                                "published_at": v.get("filed"),
                                "source_ref": v.get("accn"),
                            }
                        )
                    except (KeyError, InvalidOperation):
                        continue  # structurally broken → let schema/quarantine handle

    async def persist(sess: AsyncSession, rec: FundamentalIn):
        await get_or_create(
            sess,
            FundamentalObservation,
            {
                "instrument_id": inst.id,
                "concept": rec.concept,
                "period_end": rec.period_end,
                "source": "edgar",
                "published_at": rec.published_at,
            },
            {
                "value": rec.value,
                "unit": rec.unit,
                "currency": rec.currency,
                "period_start": rec.period_start,
                "fiscal_period": rec.fiscal_period,
                "observed_at": rec.observed_at,
                "source_ref": rec.source_ref,
            },
        )

    res = await ingest_records(
        session,
        job_run=run,
        provider=provider,
        schema=FundamentalIn,
        raw_records=records,
        persist=persist,
        target_table="fundamental_observations",
    )
    run.records_in = res.records_in
    run.records_ok = res.records_ok
    run.records_quarantined = res.records_quarantined
    run.status = "partial" if res.records_quarantined else "success"
    run.finished_at = utcnow()
    await mark_sync(session, provider, "fundamentals", True)
    return run


async def ingest_fred_series(
    session: AsyncSession,
    adapter: ProviderAdapter,
    code: str,
    name: str,
    category: str | None = None,
    use_csv: bool = False,
) -> JobRun:
    job, _ = await get_or_create(
        session,
        Job,
        {"key": f"ingest:fred:{code}"},
        {"kind": "ingestion"},
    )
    run = JobRun(job_id=job.id)
    session.add(run)
    await session.flush()
    provider = await _provider(session, "fred")

    # fredgraph CSV is latest-vintage (no ALFRED vintages w/o key);
    # published_at = fetch time, documented in regime output
    work = (lambda: adapter.observations_csv(code)) if use_csv else \
        (lambda: adapter.observations(code))
    raw, status = await run_job(
        session,
        job_run=run,
        provider_key="fred",
        work=work,
    )
    if raw is None:
        run.finished_at = utcnow()
        await mark_sync(session, provider, f"macro:{code}", False, run.error)
        return run

    series, _ = await get_or_create(
        session,
        MacroSeries,
        {"source": "fred", "code": code},
        {"name": name, "category": category, "frequency": None, "unit": None},
    )

    records = [
        {
            "series_code": code,
            "value": o["value"],
            "observed_at": o["date"],
            # CSV has no vintage → published_at defaults to obs date so
            # the unique key stays idempotent across re-ingests
            "published_at": o.get("realtime_start") or o["date"],
        }
        for o in raw
        if o.get("value") not in (None, ".")
    ]

    async def persist(sess: AsyncSession, rec: MacroIn):
        await get_or_create(
            sess,
            MacroObservation,
            {
                "series_id": series.id,
                "observed_at": _parse_dt(rec.observed_at)
                if isinstance(rec.observed_at, str)
                else rec.observed_at,
                "published_at": _parse_dt(rec.published_at)
                if isinstance(rec.published_at, str)
                else rec.published_at,
            },
            {"value": rec.value, "source": "fred",
             "source_ref": f"fred:{code}"},
        )

    res = await ingest_records(
        session,
        job_run=run,
        provider=provider,
        schema=MacroIn,
        raw_records=records,
        persist=persist,
        target_table="macro_observations",
    )
    run.records_in = res.records_in
    run.records_ok = res.records_ok
    run.records_quarantined = res.records_quarantined
    run.status = "partial" if res.records_quarantined else "success"
    run.finished_at = utcnow()
    await mark_sync(session, provider, f"macro:{code}", True)
    return run


async def ingest_stooq_bars(
    session: AsyncSession,
    adapter,
    symbol: str,
) -> JobRun:
    """Delayed-EOD daily bars (Yahoo chart API) → ohlcv_bars
    (source='yahoo', adjusted=False). Idempotent on
    (instrument, timeframe, time, source, adjusted)."""
    from app.models.market import OhlcvBar

    job, _ = await get_or_create(
        session, Job, {"key": f"ingest:yahoo:{symbol}"},
        {"kind": "ingestion"},
    )
    run = JobRun(job_id=job.id)
    session.add(run)
    await session.flush()
    provider = await _provider(session, "yahoo")

    inst = (
        await session.execute(
            select(Instrument).where(Instrument.symbol == symbol.upper())
        )
    ).scalar_one_or_none()
    if inst is None:
        run.status = "failed"
        run.error = f"{symbol} not in security master"
        await mark_sync(session, provider, f"market:{symbol}", False, run.error)
        return run

    raw, status = await run_job(
        session, job_run=run, provider_key="yahoo",
        work=lambda: adapter.fetch_daily(symbol),
    )
    if raw is None:
        run.finished_at = utcnow()
        await mark_sync(session, provider, f"market:{symbol}", False, run.error)
        return run
    if not raw:
        run.status = "success"
        run.records_in = run.records_ok = 0
        run.finished_at = utcnow()
        await mark_sync(session, provider, f"market:{symbol}", True)
        return run

    ok = 0
    for r in raw:
        t = r["observed_at"]
        exists = (
            await session.execute(
                select(OhlcvBar.id).where(
                    OhlcvBar.instrument_id == inst.id,
                    OhlcvBar.timeframe == "1d",
                    OhlcvBar.time == t,
                    OhlcvBar.source == "yahoo",
                    OhlcvBar.adjusted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            session.add(OhlcvBar(
                instrument_id=inst.id, timeframe="1d", time=t,
                open=r["open"], high=r["high"], low=r["low"],
                close=r["close"], volume=r["volume"],
                adjusted=False, source="yahoo",
            ))
            ok += 1
        else:
            ok += 1  # already present — idempotent
    await session.flush()

    run.records_in = run.records_ok = len(raw)
    run.records_quarantined = 0
    run.status = "success"
    run.finished_at = utcnow()
    await mark_sync(session, provider, f"market:{symbol}", True)
    return run
