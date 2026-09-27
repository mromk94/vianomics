from sqlalchemy import JSON, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class Dossier(Base, IdMixin, TimestampMixin):
    """Versioned 18-section research dossier (Part 6).

    All versions of one dossier share `group_id`; `version` increments.
    Rows are immutable once written — edits create a new version.
    """

    __tablename__ = "dossiers"
    __table_args__ = (
        UniqueConstraint("group_id", "version"),
        Index("ix_dossier_instr", "instrument_id", "created_at"),
    )

    group_id: Mapped[str]
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    version: Mapped[int]
    status: Mapped[str] = mapped_column(
        default="draft"
    )  # draft|review|final
    mandate_version: Mapped[int | None]
    policy_version: Mapped[int | None]
    green_zone_score: Mapped[float | None]
    created_by: Mapped[str | None]
    # [{concept, reason}] — evidence the workflow wanted but lacked
    missing_evidence: Mapped[list] = mapped_column(JSON, default=list)
    # workflow trace: plan → gather → build → validate
    workflow_log: Mapped[list] = mapped_column(JSON, default=list)


class DossierSection(Base, IdMixin, TimestampMixin):
    """One of the 18 sections. Content = list of claims:

    [{kind: paragraph|metric|list, claim_type: verified_fact|
      ai_inference|analyst_assumption|management_statement,
      text, evidence_ids: [..]}]
    """

    __tablename__ = "dossier_sections"
    __table_args__ = (UniqueConstraint("dossier_id", "section_no"),)

    dossier_id: Mapped[str] = mapped_column(
        ForeignKey("dossiers.id", ondelete="CASCADE"), index=True
    )
    section_no: Mapped[int]
    title: Mapped[str]
    # verified|partial|insufficient — roll-up of claim confidence
    status: Mapped[str] = mapped_column(default="insufficient")
    content: Mapped[list] = mapped_column(JSON)


class EvidenceItem(Base, IdMixin, TimestampMixin):
    """Evidence registry — every material claim links here."""

    __tablename__ = "evidence_items"
    __table_args__ = (Index("ix_evidence_instr", "instrument_id"),)

    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    # filing_observation|provider_datapoint|filing_ref|
    # analyst_assumption|management_statement|ai_inference
    kind: Mapped[str]
    concept: Mapped[str | None]
    value: Mapped[str | None]  # serialized — may be non-numeric
    unit: Mapped[str | None]
    period_end: Mapped[str | None]
    source: Mapped[str | None]  # provider id, accession, URL
    source_ref: Mapped[str | None]  # FundamentalObservation id etc.
    published_at: Mapped[str | None]
    note: Mapped[str | None]


class DossierReview(Base, IdMixin, TimestampMixin):
    __tablename__ = "dossier_reviews"

    dossier_id: Mapped[str] = mapped_column(
        ForeignKey("dossiers.id", ondelete="CASCADE"), index=True
    )
    reviewer_id: Mapped[str | None]
    # approve|request_changes|attest — human review record
    verdict: Mapped[str]
    note: Mapped[str | None]
