"""sleeve_state — trading-sleeve lifecycle ledger (active|cooldown,
equity high-water mark, drawdown, cooldown release audit).

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-10-04
"""
from alembic import op
import sqlalchemy as sa

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sleeve_state",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("state", sa.String(), nullable=False,
                  server_default="active"),
        sa.Column("equity_hwm", sa.Numeric(24, 8), nullable=True),
        sa.Column("drawdown_pct", sa.Numeric(10, 6), nullable=True),
        sa.Column("cooldown_at", sa.DateTime(timezone=True),
                  nullable=True),
        sa.Column("cooldown_reason", sa.String(), nullable=True),
        sa.Column("liquidated_at", sa.DateTime(timezone=True),
                  nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True),
                  nullable=True),
        sa.Column("released_by", sa.String(), nullable=True),
        sa.Column("release_note", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("sleeve_state")
