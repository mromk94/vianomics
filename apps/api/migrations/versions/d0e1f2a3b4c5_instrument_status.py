"""instrument lifecycle status — V1 Step-2 universe states

Persisted per-instrument lifecycle: watchlist | under_research |
rejected | valuation_pending | trade_eligible | active_position |
exited | cooldown. Verdicts stay computed-on-demand; this column is
the durable "where does this name sit" answer for the universe and
the audit trail.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
"""

from alembic import op
import sqlalchemy as sa

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "instruments",
        sa.Column("status", sa.String(32), nullable=False,
                  server_default="watchlist"))
    op.add_column(
        "instruments",
        sa.Column("status_at", sa.DateTime(timezone=True),
                  nullable=True))
    op.create_index("ix_instruments_status", "instruments", ["status"])


def downgrade() -> None:
    op.drop_index("ix_instruments_status", table_name="instruments")
    op.drop_column("instruments", "status_at")
    op.drop_column("instruments", "status")
