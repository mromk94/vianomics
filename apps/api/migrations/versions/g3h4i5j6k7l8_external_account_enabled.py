"""external_accounts.enabled — per-source kill switch.

A source can stay connected (pushes keep landing, snapshots stay
fresh) while being excluded from every read path: portfolio NAV/cash,
risk sizing, pyramid context, Command Center stats. Toggling back on
restores it without reconnecting.

Revision ID: g3h4i5j6k7l8
Revises: f2a3b4c5d6e7
"""
from alembic import op
import sqlalchemy as sa

revision = "g3h4i5j6k7l8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "external_accounts",
        sa.Column("enabled", sa.Boolean(), nullable=False,
                  server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("external_accounts", "enabled")
