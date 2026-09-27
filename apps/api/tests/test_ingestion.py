from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.ingestion.runner import freshness, ingest_records, run_job
from app.ingestion.schemas import MacroIn
from app.ingestion.upsert import get_or_create, supersede
from app.models.market import MacroObservation, MacroSeries
from app.models.ops import Job, JobRun, QuarantinedRecord, SyncStatus
from app.models.providers import DataProvider
from app.providers.base import ProviderConfigError, ProviderError


async def _job_run(db) -> JobRun:
    job = Job(key="test:job", kind="ingestion")
    db.add(job)
    await db.flush()
    run = JobRun(job_id=job.id)
    db.add(run)
    await db.flush()
    return run


async def test_invalid_records_are_quarantined(db):
    run = await _job_run(db)
    series = MacroSeries(source="fred", code="GDP", name="GDP")
    db.add(series)
    await db.flush()

    async def persist(sess, rec: MacroIn):
        await get_or_create(
            sess,
            MacroObservation,
            {
                "series_id": series.id,
                "observed_at": rec.observed_at,
                "published_at": rec.published_at,
            },
            {"value": rec.value, "source": "fred"},
        )

    raw = [
        {"series_code": "GDP", "value": "27000", "observed_at": "2024-01-01"},
        {"series_code": "GDP", "value": "not-a-number",
         "observed_at": "2024-01-01"},  # invalid → quarantine
        {"series_code": "GDP"},          # missing fields → quarantine
    ]
    res = await ingest_records(
        db, job_run=run, provider=None, schema=MacroIn,
        raw_records=raw, persist=persist,
        target_table="macro_observations",
    )
    await db.commit()
    assert res.records_in == 3
    assert res.records_ok == 1
    assert res.records_quarantined == 2
    q = (
        await db.execute(select(func.count(QuarantinedRecord.id)))
    ).scalar()
    assert q == 2


async def test_ingestion_is_idempotent(db):
    run = await _job_run(db)
    series = MacroSeries(source="fred", code="CPI", name="CPI")
    db.add(series)
    await db.flush()

    async def persist(sess, rec: MacroIn):
        await get_or_create(
            sess, MacroObservation,
            {"series_id": series.id, "observed_at": rec.observed_at,
             "published_at": rec.published_at},
            {"value": rec.value, "source": "fred"},
        )

    raw = [{"series_code": "CPI", "value": "310",
            "observed_at": "2024-01-01"}]
    await ingest_records(db, job_run=run, provider=None, schema=MacroIn,
                         raw_records=raw, persist=persist,
                         target_table="macro_observations")
    await ingest_records(db, job_run=run, provider=None, schema=MacroIn,
                         raw_records=raw, persist=persist,
                         target_table="macro_observations")
    await db.commit()
    n = (await db.execute(select(func.count(MacroObservation.id)))).scalar()
    assert n == 1  # rerun produced no duplicate


async def test_supersede_inserts_revision(db):
    inst_key = {"series_id": "s1", "observed_at": datetime(2024, 1, 1, tzinfo=UTC)}
    r1, s = await supersede(db, MacroObservation, inst_key,
                            {"value": Decimal("1"), "source": "fred"})
    assert s == "inserted"
    r2, s = await supersede(db, MacroObservation, inst_key,
                            {"value": Decimal("2"), "source": "fred"})
    assert s == "revised"
    assert r2.supersedes_id == r1.id
    r3, s = await supersede(db, MacroObservation, inst_key,
                            {"value": Decimal("2"), "source": "fred"})
    # natural key match on latest row: value unchanged relative to r2
    assert s in ("unchanged", "revised")
    await db.commit()


async def test_run_job_retries_then_succeeds(db):
    run = await _job_run(db)
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ProviderError("upstream 500")
        return [{"ok": True}]

    recs, status = await run_job(
        db, job_run=run, provider_key="test", work=flaky
    )
    assert status == "success" and calls["n"] == 3


async def test_run_job_never_retries_config_error(db):
    run = await _job_run(db)

    async def bad():
        raise ProviderConfigError("no key")

    recs, status = await run_job(
        db, job_run=run, provider_key="test", work=bad
    )
    assert status == "failed" and recs is None and run.attempt == 1


def test_freshness_states():
    now = datetime.now(UTC)
    s = SyncStatus(provider_id="p", dataset="d")
    assert freshness(s, 60) == "expired"
    s.last_success_at = now
    assert freshness(s, 60) == "fresh"
    s.last_success_at = now.replace(year=now.year - 1)
    assert freshness(s, 60) == "expired"
