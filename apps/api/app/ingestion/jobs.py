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
        # self-heal: SEC's official ticker→CIK map; persist the
        # identifier so subsequent runs skip the lookup
        try:
            tmap = await adapter.ticker_map()
            hit = next(
                (v for v in tmap.values()
                 if str(v.get("ticker", "")).upper() == symbol.upper()),
                None)
            if hit:
                cik = str(hit["cik_str"])
                session.add(InstrumentIdentifier(
                    instrument_id=inst.id, scheme="cik", value=cik))
                await session.flush()
        except Exception:
            cik = None
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

    # set-based dedupe — one SELECT instead of a remote round-trip
    # per observation (companyfacts returns tens of thousands).
    # DB returns tz-aware datetimes; parsed SEC dates are naive →
    # normalize to aware-UTC before key comparison or re-runs collide
    # on the unique constraint instead of deduping.
    def _aware(dt):
        return (dt.replace(tzinfo=UTC)
                if dt is not None and dt.tzinfo is None else dt)

    def _key(concept, period_end, published_at):
        return (concept, period_end.isoformat(),
                _aware(published_at).isoformat() if published_at else None)

    existing = {
        _key(r[0], r[1], r[2]) for r in (await session.execute(
            select(FundamentalObservation.concept,
                   FundamentalObservation.period_end,
                   FundamentalObservation.published_at)
            .where(FundamentalObservation.instrument_id == inst.id))
        ).all()
    }

    async def persist(sess: AsyncSession, rec: FundamentalIn):
        key = _key(rec.concept, rec.period_end, rec.published_at)
        if key in existing:
            return
        existing.add(key)
        sess.add(FundamentalObservation(
            instrument_id=inst.id,
            concept=rec.concept,
            period_end=rec.period_end,
            source="edgar",
            value=rec.value,
            unit=rec.unit,
            currency=rec.currency,
            period_start=rec.period_start,
            fiscal_period=rec.fiscal_period,
            observed_at=_aware(rec.observed_at),
            published_at=_aware(rec.published_at),
            source_ref=rec.source_ref,
        ))

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


async def ingest_yahoo_fundamentals(
    session: AsyncSession,
    adapter: ProviderAdapter,
    symbol: str,
) -> JobRun:
    """Yahoo fundamentals-timeseries → FundamentalObservations under
    `yahoo:` concepts — the coverage bridge for issuers SEC EDGAR
    doesn't carry (non-filers, some ADRs) and the fill for concepts a
    filer's taxonomy lacks. Same canonical metrics the screen reads;
    source='yahoo' keeps provenance distinct from 'edgar' XBRL.

    published_at is stamped with the reported asOfDate — deterministic
    so the unique key dedupes re-runs; screening runs as-of-now so the
    (mildly optimistic) vintage is acceptable, but backtests should not
    treat these as filed-date-accurate."""
    from datetime import timedelta
    from app.providers.yahoo import FUNDAMENTAL_TYPES

    job, _ = await get_or_create(
        session,
        Job,
        {"key": f"ingest:yahoo:fundamentals:{symbol}"},
        {"kind": "ingestion"},
    )
    run = JobRun(job_id=job.id)
    session.add(run)
    await session.flush()

    provider = await _provider(session, "yahoo")
    inst = await _instrument(session, symbol)
    if inst is None:
        run.status, run.error, run.finished_at = (
            "failed", f"instrument {symbol} not found", utcnow())
        return run

    raw, _status = await run_job(
        session, job_run=run, provider_key="yahoo",
        work=lambda: adapter.fundamentals_timeseries(symbol),
    )
    if raw is None:
        run.finished_at = utcnow()
        await mark_sync(session, provider, f"fundamentals:{symbol}",
                        False, run.error)
        return run

    # Yahoo outflow signs are negative; the engines use the us-gaap
    # positive-outflow convention (fcf = ocf − capex)
    _OUTFLOW = {"annualCapitalExpenditure", "annualCashDividendsPaid",
                "annualDividendsPaid"}
    _ABS = _OUTFLOW | {"annualInterestExpense",
                       "annualInterestExpenseNonOperating"}

    def _aware(dt):
        return (dt.replace(tzinfo=UTC)
                if dt is not None and dt.tzinfo is None else dt)

    existing = {
        (r[0], r[1].isoformat(),
         r[2].date().isoformat() if r[2] else None)
        for r in (await session.execute(
            select(FundamentalObservation.concept,
                   FundamentalObservation.period_end,
                   FundamentalObservation.published_at)
            .where(FundamentalObservation.instrument_id == inst.id))
        ).all()
    }

    n_in = n_ok = 0
    for ytype, concept in FUNDAMENTAL_TYPES.items():
        series = raw.get(ytype + "__series") or []
        for pt in series:
            end_s = pt.get("asOfDate")
            if not end_s:
                continue
            end = _parse_date(end_s)
            val = float(pt["value"])
            if ytype in _ABS:
                val = abs(val)
            is_flow = pt.get("periodType") == "12M"
            pstart = (end - timedelta(days=364)) if is_flow else None
            n_in += 1
            key = (concept, end.isoformat(), end.isoformat())
            if key in existing:
                continue
            existing.add(key)
            ccy = pt.get("currencyCode")
            session.add(FundamentalObservation(
                instrument_id=inst.id,
                concept=concept,
                period_start=pstart,
                period_end=end,
                fiscal_period="FY",
                source="yahoo",
                value=Decimal(str(val)),
                unit=("shares" if "Shares" in concept else
                      (ccy or "USD") + ("/share" if "EPS" in concept
                                        else "")),
                currency=ccy if "Shares" not in concept else None,
                observed_at=_aware(datetime.combine(
                    end, datetime.min.time())),
                published_at=_aware(datetime.combine(
                    end, datetime.min.time())),
                source_ref="yahoo:fundamentals-timeseries",
            ))
            n_ok += 1
    await session.flush()

    run.records_in = n_in
    run.records_ok = n_ok
    run.records_quarantined = 0
    run.status = "success"
    run.finished_at = utcnow()
    await mark_sync(session, provider, f"fundamentals:{symbol}", True)
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
        if o.get("value") not in (None, "", ".")
    ]

    # bulk dedupe — one query for existing (observed_at, published_at)
    # keys, then add_all. Per-record get_or_create over a remote DB
    # turns a 10K-row series into minutes of round-trips.
    def _aware(dt):
        return (dt.replace(tzinfo=UTC)
                if dt is not None and dt.tzinfo is None else dt)

    existing = {
        (_aware(o.observed_at), _aware(o.published_at))
        for o in (await session.execute(
            select(MacroObservation)
            .where(MacroObservation.series_id == series.id))
        ).scalars().all()
    }

    async def persist(sess: AsyncSession, rec: MacroIn):
        obs_at = _aware(_parse_dt(rec.observed_at)
                        if isinstance(rec.observed_at, str)
                        else rec.observed_at)
        pub_at = _aware(_parse_dt(rec.published_at)
                        if isinstance(rec.published_at, str)
                        else rec.published_at)
        if (obs_at, pub_at) in existing:
            return
        sess.add(MacroObservation(
            series_id=series.id, observed_at=obs_at,
            published_at=pub_at, value=rec.value, source="fred",
            source_ref=f"fred:{code}"))
        existing.add((obs_at, pub_at))

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


# release-name matchers → which FRED releases matter to the desk.
# Substring match against release_name — tolerant to FRED renames.
WATCH_RELEASES = [
    "employment situation", "consumer price index",
    "producer price index", "gross domestic product",
    "industrial production", "consumer sentiment",
    "retail sales", "federal open market",
    "personal income", "housing starts",
]


async def ingest_fred_calendar(
    session: AsyncSession,
    adapter: ProviderAdapter,
    days_ahead: int = 45,
) -> JobRun | None:
    """Upcoming macro releases → economic_releases. Needs a FRED key;
    without one the job degrades silently (calendar is optional)."""
    from datetime import timedelta
    from app.models.market import EconomicRelease

    job, _ = await get_or_create(
        session, Job, {"key": "ingest:fred:calendar"},
        {"kind": "ingestion"})
    run = JobRun(job_id=job.id)
    session.add(run)
    await session.flush()
    provider = await _provider(session, "fred")

    today = date.today()
    try:
        raw = await adapter.release_dates(
            today.isoformat(),
            (today + timedelta(days=days_ahead)).isoformat())
    except Exception as e:
        run.status, run.error, run.finished_at = (
            "failed", str(e)[:200], utcnow())
        await mark_sync(session, provider, "macro:calendar", False,
                        run.error)
        return run
    if raw is None:
        run.status = "failed"
        run.finished_at = utcnow()
        return run

    keep = [r for r in raw if any(
        w in (r.get("release_name") or "").lower()
        for w in WATCH_RELEASES)]
    ok = 0
    for r in keep:
        rel_at = _parse_dt(r["date"])
        exists = (await session.execute(
            select(EconomicRelease.id).where(
                EconomicRelease.title == r["release_name"],
                EconomicRelease.release_at == rel_at))
        ).scalar_one_or_none()
        if exists is None:
            session.add(EconomicRelease(
                title=r["release_name"], release_at=rel_at,
                source="fred"))
            ok += 1
    await session.flush()
    run.records_in = len(keep)
    run.records_ok = ok
    run.records_quarantined = 0
    run.status = "success"
    run.finished_at = utcnow()
    await mark_sync(session, provider, "macro:calendar", True)
    return run


async def ingest_stooq_bars(
    session: AsyncSession,
    adapter,
    symbol: str,
    start: str = "2000-01-01",
) -> JobRun:
    """Delayed-EOD daily bars (Yahoo chart API) → ohlcv_bars
    (source='yahoo', adjusted=False). Idempotent on
    (instrument, timeframe, time, source, adjusted). `start` bounds
    the fetch window — the staleness self-heal passes a tail date."""
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
        work=lambda: adapter.fetch_daily(symbol, start=start),
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

    # bulk dedupe — one query for existing bar times; per-row SELECTs
    # over a remote DB turn a 500-bar ingest into minutes of latency
    existing_times = {
        t for (t,) in (await session.execute(
            select(OhlcvBar.time).where(
                OhlcvBar.instrument_id == inst.id,
                OhlcvBar.timeframe == "1d",
                OhlcvBar.source == "yahoo",
                OhlcvBar.adjusted.is_(False)))).all()
    }
    ok = 0
    today = utcnow().date()
    for r in raw:
        t = r["observed_at"]
        if t not in existing_times:
            session.add(OhlcvBar(
                instrument_id=inst.id, timeframe="1d", time=t,
                open=r["open"], high=r["high"], low=r["low"],
                close=r["close"], volume=r["volume"],
                adjusted=False, source="yahoo",
            ))
            existing_times.add(t)
        elif t.date() >= today:
            # today's bar is still forming — an earlier ingest stored
            # a partial bar; refresh it in place so the "close" isn't
            # frozen at mid-session values
            row = (await session.execute(
                select(OhlcvBar).where(
                    OhlcvBar.instrument_id == inst.id,
                    OhlcvBar.timeframe == "1d",
                    OhlcvBar.time == t,
                    OhlcvBar.source == "yahoo",
                    OhlcvBar.adjusted.is_(False)))).scalar_one_or_none()
            if row:
                row.open, row.high, row.low = r["open"], r["high"], r["low"]
                row.close, row.volume = r["close"], r["volume"]
        ok += 1
    await session.flush()

    run.records_in = run.records_ok = len(raw)
    run.records_quarantined = 0
    run.status = "success"
    run.finished_at = utcnow()
    await mark_sync(session, provider, f"market:{symbol}", True)
    return run


async def ingest_tiingo_bars(
    session: AsyncSession,
    adapter,
    symbol: str,
    start: str = "2015-01-01",
) -> JobRun:
    """Tiingo EOD bars → ohlcv_bars (source='tiingo', adjusted=False).
    Idempotent on (instrument, timeframe, time, source, adjusted)."""
    from app.models.market import OhlcvBar

    job, _ = await get_or_create(
        session, Job, {"key": f"ingest:tiingo:{symbol}"},
        {"kind": "ingestion"})
    run = JobRun(job_id=job.id)
    session.add(run)
    await session.flush()
    provider = await _provider(session, "tiingo")

    inst = (
        await session.execute(
            select(Instrument).where(Instrument.symbol == symbol.upper())
        )
    ).scalar_one_or_none()
    if inst is None:
        run.status, run.error, run.finished_at = (
            "failed", f"{symbol} not in security master", utcnow())
        await mark_sync(session, provider, f"market:{symbol}", False,
                        run.error)
        return run

    today = date.today().isoformat()
    raw, status = await run_job(
        session, job_run=run, provider_key="tiingo",
        work=lambda: adapter.eod(symbol, start, today))
    if raw is None:
        run.finished_at = utcnow()
        await mark_sync(session, provider, f"market:{symbol}", False,
                        run.error)
        return run

    existing_times = {
        t for (t,) in (await session.execute(
            select(OhlcvBar.time).where(
                OhlcvBar.instrument_id == inst.id,
                OhlcvBar.timeframe == "1d",
                OhlcvBar.source == "tiingo",
                OhlcvBar.adjusted.is_(False)))).all()
    }
    for r in raw:
        t = _parse_dt(r["date"])
        if t not in existing_times:
            session.add(OhlcvBar(
                instrument_id=inst.id, timeframe="1d", time=t,
                open=r["open"], high=r["high"], low=r["low"],
                close=r["close"], volume=r.get("volume"),
                adjusted=False, source="tiingo"))
            existing_times.add(t)
    await session.flush()
    run.records_in = run.records_ok = len(raw)
    run.records_quarantined = 0
    run.status = "success"
    run.finished_at = utcnow()
    await mark_sync(session, provider, f"market:{symbol}", True)
    return run


async def ingest_tiingo_intraday(
    session: AsyncSession,
    adapter,
    symbol: str,
    freq: str = "30min",
    days: int = 10,
) -> JobRun:
    """Tiingo IEX intraday bars → ohlcv_bars (source='tiingo',
    timeframe=freq e.g. '30min'). Also refreshes the symbol's
    market_quotes row from the latest bar close — intraday is the
    nearest thing to a live tape without a streaming feed."""
    from datetime import timedelta
    from app.models.market import MarketQuote, OhlcvBar

    job, _ = await get_or_create(
        session, Job,
        {"key": f"ingest:tiingo:intraday:{symbol}:{freq}"},
        {"kind": "ingestion"})
    run = JobRun(job_id=job.id)
    session.add(run)
    await session.flush()
    provider = await _provider(session, "tiingo")

    inst = (
        await session.execute(
            select(Instrument).where(Instrument.symbol == symbol.upper())
        )
    ).scalar_one_or_none()
    if inst is None:
        run.status, run.error, run.finished_at = (
            "failed", f"{symbol} not in security master", utcnow())
        await mark_sync(session, provider, f"intraday:{symbol}", False,
                        run.error)
        return run

    start = (date.today() - timedelta(days=days)).isoformat()
    raw, status = await run_job(
        session, job_run=run, provider_key="tiingo",
        work=lambda: adapter.intraday(symbol, start, freq))
    if raw is None:
        run.finished_at = utcnow()
        await mark_sync(session, provider, f"intraday:{symbol}", False,
                        run.error)
        return run

    existing_times = {
        (t if t.tzinfo else t.replace(tzinfo=UTC))
        for (t,) in (await session.execute(
            select(OhlcvBar.time).where(
                OhlcvBar.instrument_id == inst.id,
                OhlcvBar.timeframe == freq,
                OhlcvBar.source == "tiingo",
                OhlcvBar.adjusted.is_(False)))).all()
    }
    last_close = None
    for r in raw:
        t = _parse_dt(r["date"])
        last_close = r.get("close")
        if t not in existing_times:
            session.add(OhlcvBar(
                instrument_id=inst.id, timeframe=freq, time=t,
                open=r["open"], high=r["high"], low=r["low"],
                close=r["close"], volume=r.get("volume"),
                adjusted=False, source="tiingo"))
            existing_times.add(t)
    await session.flush()

    # last intraday bar → quote row (source='tiingo')
    if last_close:
        now = utcnow()
        qrow = (await session.execute(
            select(MarketQuote).where(
                MarketQuote.source == "tiingo",
                MarketQuote.symbol == symbol.upper()))
        ).scalar_one_or_none()
        if qrow is None:
            qrow = MarketQuote(source="tiingo",
                               symbol=symbol.upper(), ts=now)
            session.add(qrow)
        qrow.instrument_id = inst.id
        qrow.mid = qrow.bid = qrow.ask = last_close
        qrow.ts = now
    await session.flush()

    run.records_in = run.records_ok = len(raw)
    run.records_quarantined = 0
    run.status = "success"
    run.finished_at = utcnow()
    await mark_sync(session, provider, f"intraday:{symbol}", True)
    return run


# Alpaca timeframe strings → the canonical codes every engine reads
# ('1Day' must land as '1d' — otherwise bars ingest successfully and
# are invisible to ATR/technical/sleeve)
_ALPACA_TF = {"1min": "1m", "5min": "5m", "15min": "15m",
              "30min": "30m", "1hour": "1h", "4hour": "4h",
              "1day": "1d", "1week": "1w", "1month": "1mo"}


async def ingest_alpaca_bars(
    session: AsyncSession,
    adapter,
    symbol: str,
    start: str = "2015-01-01",
    timeframe: str = "1Day",
) -> JobRun:
    """Alpaca stock bars → ohlcv_bars (source='alpaca', canonical
    timeframe). Idempotent; for daily+ frames dedupes by SESSION DATE
    across ALL sources so a yahoo-covered day is never duplicated by
    an alpaca bar (two rows for one session would corrupt ATR)."""
    from app.models.market import OhlcvBar

    job, _ = await get_or_create(
        session, Job, {"key": f"ingest:alpaca:{symbol}:{timeframe}"},
        {"kind": "ingestion"})
    run = JobRun(job_id=job.id)
    session.add(run)
    await session.flush()
    provider = await _provider(session, "alpaca")

    inst = await _instrument(session, symbol)
    if inst is None:
        run.status, run.error, run.finished_at = (
            "failed", f"{symbol} not in security master", utcnow())
        await mark_sync(session, provider, f"market:{symbol}", False,
                        run.error)
        return run

    today = date.today().isoformat()
    raw, status = await run_job(
        session, job_run=run, provider_key="alpaca",
        work=lambda: adapter.bars(symbol, start, today,
                                  timeframe=timeframe))
    if raw is None:
        run.finished_at = utcnow()
        await mark_sync(session, provider, f"market:{symbol}", False,
                        run.error)
        return run

    tf = _ALPACA_TF.get(timeframe.lower(), timeframe.lower())
    daily = tf in ("1d", "1w", "1mo")
    if daily:
        # one row per session, whichever source wrote it first —
        # mixed-source history is fine (source column is the audit),
        # two rows for the same day is not
        existing_dates = {
            t.date() for (t,) in (await session.execute(
                select(OhlcvBar.time).where(
                    OhlcvBar.instrument_id == inst.id,
                    OhlcvBar.timeframe == tf))).all()}
    else:
        existing_dates = {
            t for (t,) in (await session.execute(
                select(OhlcvBar.time).where(
                    OhlcvBar.instrument_id == inst.id,
                    OhlcvBar.timeframe == tf,
                    OhlcvBar.source == "alpaca"))).all()}
    for r in raw:
        t = _parse_dt(r["t"])
        key = t.date() if daily else t
        if key not in existing_dates:
            session.add(OhlcvBar(
                instrument_id=inst.id, timeframe=tf, time=t,
                open=r["o"], high=r["h"], low=r["l"],
                close=r["c"], volume=r.get("v"),
                adjusted=False, source="alpaca"))
            existing_dates.add(key)
    await session.flush()
    run.records_in = run.records_ok = len(raw)
    run.records_quarantined = 0
    run.status = "success"
    run.finished_at = utcnow()
    await mark_sync(session, provider, f"market:{symbol}", True)
    return run
