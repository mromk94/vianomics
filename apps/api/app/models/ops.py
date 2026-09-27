from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TenantMixin, TimestampMixin, utcnow


class Alert(Base, IdMixin, TenantMixin, TimestampMixin):
    __tablename__ = "alerts"

    severity: Mapped[str]  # info|warning|critical
    source: Mapped[str]  # monitoring|ingestion|risk|pyramid…
    message: Mapped[str]
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(
        default="active", index=True
    )  # active|acknowledged|resolved
    acked_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )


class Job(Base, IdMixin, TimestampMixin):
    """Registered background job definition."""

    __tablename__ = "jobs"

    key: Mapped[str] = mapped_column(unique=True)  # ingest:fred:cpi
    kind: Mapped[str]  # ingestion|monitoring|maintenance
    schedule: Mapped[str | None]  # cron-ish or interval seconds
    provider_id: Mapped[str | None] = mapped_column(
        ForeignKey("data_providers.id")
    )
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(default=True)


class JobRun(Base, IdMixin, TimestampMixin):
    __tablename__ = "job_runs"

    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(
        default="running", index=True
    )  # running|success|partial|failed
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    attempt: Mapped[int] = mapped_column(default=1)
    records_in: Mapped[int] = mapped_column(default=0)
    records_ok: Mapped[int] = mapped_column(default=0)
    records_quarantined: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None]
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class SyncStatus(Base, IdMixin, TimestampMixin):
    """Per-provider freshness + last successful sync. Drives the
    'stale' data-quality flag and provider health UI."""

    __tablename__ = "sync_status"
    __table_args__ = (UniqueConstraint("provider_id", "dataset"),)

    provider_id: Mapped[str] = mapped_column(
        ForeignKey("data_providers.id", ondelete="CASCADE"), index=True
    )
    dataset: Mapped[str]  # fundamentals|ohlcv_1d|macro:cpi…
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_error: Mapped[str | None]
    lag_seconds: Mapped[int | None]


class QuarantinedRecord(Base, IdMixin, TimestampMixin):
    """Invalid observations land here — rejected but inspectable,
    never silently dropped or coerced."""

    __tablename__ = "quarantined_records"

    job_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("job_runs.id", ondelete="SET NULL")
    )
    provider_id: Mapped[str | None] = mapped_column(
        ForeignKey("data_providers.id")
    )
    target_table: Mapped[str]
    raw: Mapped[dict] = mapped_column(JSON)
    errors: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(default="quarantined")  # quarantined|reprocessed|dismissed
