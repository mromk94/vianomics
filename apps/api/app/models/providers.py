from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class DataProvider(Base, IdMixin, TimestampMixin):
    __tablename__ = "data_providers"

    key: Mapped[str] = mapped_column(unique=True)  # edgar|fred|tiingo|ibkr…
    name: Mapped[str]
    kind: Mapped[str]  # fundamentals|market|macro|news|broker
    # configured means credentials present; never claim 'connected' until a
    # real integration test succeeded — see SyncStatus.last_success_at.
    status: Mapped[str] = mapped_column(
        default="unconfigured"
    )  # unconfigured|configured|connected|degraded|down
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    rate_limit: Mapped[dict] = mapped_column(JSON, default=dict)


class ProviderCredentialMeta(Base, IdMixin, TimestampMixin):
    """Metadata about credentials — NEVER the secret itself.
    secret_ref points into an env var / secret manager entry."""

    __tablename__ = "provider_credential_meta"
    __table_args__ = (UniqueConstraint("provider_id", "key"),)

    provider_id: Mapped[str] = mapped_column(
        ForeignKey("data_providers.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str]  # e.g. "api_key", "account_id"
    secret_ref: Mapped[str]  # e.g. "env:TIINGO_API_KEY" — reference only
    rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
