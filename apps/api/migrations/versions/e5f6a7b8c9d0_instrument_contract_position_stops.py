"""instrument contract spec + position stop/target/fair_value/strategy

VAIIP Instrument Master (spec phase 1) + live position risk fields
(position ledger) — never hard-code contractSize=1.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
"""
import sqlalchemy as sa
from alembic import op

revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("instruments",
                  sa.Column("contract_size", sa.Numeric(24, 8),
                            nullable=False, server_default="1"))
    op.add_column("instruments",
                  sa.Column("tick_size", sa.Numeric(24, 8), nullable=True))
    op.add_column("instruments",
                  sa.Column("tick_value", sa.Numeric(24, 8), nullable=True))
    op.add_column("instruments",
                  sa.Column("margin_rate", sa.Numeric(10, 6),
                            nullable=True))
    op.add_column("instruments",
                  sa.Column("maintenance_margin_rate", sa.Numeric(10, 6),
                            nullable=True))
    op.add_column("positions",
                  sa.Column("stop_price", sa.Numeric(28, 8), nullable=True))
    op.add_column("positions",
                  sa.Column("target_price", sa.Numeric(28, 8),
                            nullable=True))
    op.add_column("positions",
                  sa.Column("fair_value", sa.Numeric(28, 8), nullable=True))
    op.add_column("positions",
                  sa.Column("strategy", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("positions", "strategy")
    op.drop_column("positions", "fair_value")
    op.drop_column("positions", "target_price")
    op.drop_column("positions", "stop_price")
    op.drop_column("instruments", "maintenance_margin_rate")
    op.drop_column("instruments", "margin_rate")
    op.drop_column("instruments", "tick_value")
    op.drop_column("instruments", "tick_size")
    op.drop_column("instruments", "contract_size")
