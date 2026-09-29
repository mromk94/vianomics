"""equity_history on external_accounts

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""
import sqlalchemy as sa
from alembic import op

revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("external_accounts",
                  sa.Column("equity_history", sa.JSON(), nullable=False,
                            server_default="[]"))


def downgrade() -> None:
    op.drop_column("external_accounts", "equity_history")
