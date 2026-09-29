"""technical_scan_results table

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
"""
import sqlalchemy as sa
from alembic import op

revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "technical_scan_results",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("instrument_id", sa.String(),
                  sa.ForeignKey("instruments.id"), index=True,
                  nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("engine", sa.String(), nullable=False),
        sa.Column("indicators", sa.JSON(), nullable=True),
        sa.Column("last_close", sa.Float(), nullable=True),
        sa.Column("data_fresh", sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tsr_instr", "technical_scan_results",
                    ["instrument_id", "created_at"])


def downgrade() -> None:
    op.drop_table("technical_scan_results")
