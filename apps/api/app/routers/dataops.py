from fastapi import APIRouter, Depends
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
