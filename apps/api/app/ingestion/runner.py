"""Ingestion job runner.

Responsibilities:
- per-provider throttling (token-budget via simple sleep)
- retry with backoff on ProviderError; no retry on ProviderConfigError
- every record validated → normalized → persisted; invalid → quarantine
- JobRun bookkeeping: in/ok/quarantined counts; SyncStatus freshness
- idempotent: re-running produces no duplicates
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.ingestion.upsert import get_or_create
from app.models.ops import JobRun, QuarantinedRecord, SyncStatus
from app.models.providers import DataProvider
from app.providers.base import ProviderConfigError, ProviderError

# provider key → min seconds between requests
DEFAULT_RPS: dict[str, float] = {"edgar": 0.5, "fred": 0.5, "tiingo": 0.2}
_last_call: dict[str, float] = {}


async def throttle(provider_key: str) -> None:
    gap = DEFAULT_RPS.get(provider_key, 0.5)
    last = _last_call.get(provider_key, 0.0)
    wait = gap - (time.monotonic() - last)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_call[provider_key] = time.monotonic()


class IngestResult(BaseModel):
    records_in: int = 0
    records_ok: int = 0
    records_quarantined: int = 0


async def ingest_records(
    session: AsyncSession,
    *,
    job_run: JobRun,
    provider: DataProvider | None,
    schema: type[BaseModel],
    raw_records: list[dict[str, Any]],
    persist: Callable[[AsyncSession, BaseModel], Awaitable[Any]],
    target_table: str,
) -> IngestResult:
    """Validate each raw record; persist valid; quarantine invalid."""
    res = IngestResult(records_in=len(raw_records))
    for raw in raw_records:
        try:
            rec = schema.model_validate(raw)
        except ValidationError as e:
            session.add(
                QuarantinedRecord(
                    job_run_id=job_run.id,
                    provider_id=provider.id if provider else None,
                    target_table=target_table,
                    raw=raw if isinstance(raw, dict) else {"raw": str(raw)},
                    errors=e.errors(),
                )
            )
            res.records_quarantined += 1
            continue
        await persist(session, rec)
        res.records_ok += 1
    return res


async def run_job(
    session: AsyncSession,
    *,
    job_run: JobRun,
    provider_key: str,
    work: Callable[[], Awaitable[list[dict[str, Any]]]],
    max_retries: int = 3,
) -> tuple[list[dict[str, Any]] | None, str]:
    """Fetch with retry/backoff. Returns (records, status)."""
    attempt = 0
    while True:
        attempt += 1
        job_run.attempt = attempt
        try:
            await throttle(provider_key)
            records = await work()
            return records, "success"
        except ProviderConfigError as e:
            job_run.status = "failed"
            job_run.error = str(e)
            return None, "failed"
        except ProviderError as e:
            if attempt >= max_retries:
                job_run.status = "failed"
                job_run.error = str(e)
                return None, "failed"
            await asyncio.sleep(min(2 ** attempt, 30))


async def mark_sync(
    session: AsyncSession,
    provider: DataProvider,
    dataset: str,
    ok: bool,
    error: str | None = None,
) -> None:
    now = utcnow()
    row, _ = await get_or_create(
        session, SyncStatus, {"provider_id": provider.id, "dataset": dataset}
    )
    row.last_attempt_at = now
    if ok:
        row.last_success_at = now
        row.last_error = None
        provider.status = "connected"
    else:
        row.last_error = error
        provider.status = "degraded"


def freshness(sync: SyncStatus, max_age_seconds: int) -> str:
    """fresh|stale|expired — drives data-quality flags downstream."""
    if sync.last_success_at is None:
        return "expired"
    lag = (datetime.now(UTC) - sync.last_success_at).total_seconds()
    if lag <= max_age_seconds:
        return "fresh"
    if lag <= max_age_seconds * 4:
        return "stale"
    return "expired"
