"""external accounts (MT4/Bamboo pushed snapshots)

Revision ID: a1b2c3d4e5f6
Revises: 950884fdbcc2
"""
import sqlalchemy as sa
from alembic import op

revision = 'a1b2c3d4e5f6'
down_revision = '950884fdbcc2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_accounts",
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False,
                  server_default="USD"),
        sa.Column("balance", sa.Numeric(28, 8), nullable=True),
        sa.Column("equity", sa.Numeric(28, 8), nullable=True),
        sa.Column("positions", sa.JSON(), nullable=False,
                  server_default="[]"),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_external_accounts_source",
                    "external_accounts", ["source"])


def downgrade() -> None:
    op.drop_index("ix_external_accounts_source",
                  table_name="external_accounts")
    op.drop_table("external_accounts")
