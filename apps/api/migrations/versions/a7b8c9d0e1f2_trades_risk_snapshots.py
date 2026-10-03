"""trade_ideas table (formal trade object, spec §4/§33) + risk_snapshots
table (periodic book risk state) + instrument trading_hours

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2025-10-04
"""
from alembic import op
import sqlalchemy as sa

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("instruments", sa.Column("trading_hours", sa.JSON(),
                                           nullable=True))
    op.create_table(
        "trade_ideas",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("instrument_id", sa.String(),
                  sa.ForeignKey("instruments.id"), nullable=False),
        sa.Column("risk_check_id", sa.String(),
                  sa.ForeignKey("risk_checks.id"), nullable=True),
        sa.Column("strategy", sa.String(), nullable=True),
        sa.Column("direction", sa.String(), server_default="long"),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("stop_price", sa.Float(), nullable=False),
        sa.Column("target_price", sa.Float(), nullable=True),
        sa.Column("fair_value", sa.Float(), nullable=True),
        sa.Column("proposed_quantity", sa.Float(), nullable=False),
        sa.Column("thesis", sa.String(), nullable=True),
        sa.Column("thesis_status", sa.String(), server_default="REVIEW"),
        sa.Column("status", sa.String(), server_default="REVIEW"),
        sa.Column("sheet", sa.JSON(), nullable=True),
        sa.Column("engine_version", sa.String(),
                  server_default="risk-pyramid/v2.0"),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )
    op.create_index("ix_trade_ideas_instr", "trade_ideas", ["instrument_id"])
    op.create_table(
        "risk_snapshots",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.Column("cash", sa.Float(), nullable=True),
        sa.Column("gross_exposure", sa.Float(), nullable=True),
        sa.Column("net_exposure", sa.Float(), nullable=True),
        sa.Column("leverage", sa.Float(), nullable=True),
        sa.Column("margin_used", sa.Float(), nullable=True),
        sa.Column("margin_utilisation", sa.Float(), nullable=True),
        sa.Column("open_risk", sa.Float(), nullable=True),
        sa.Column("open_risk_pct", sa.Float(), nullable=True),
        sa.Column("risk_capacity", sa.Float(), nullable=True),
        sa.Column("drawdown_pct", sa.Float(), nullable=True),
        sa.Column("var_95", sa.Float(), nullable=True),
        sa.Column("stress_loss", sa.Float(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("engine_version", sa.String(),
                  server_default="risk-pyramid/v2.0"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("risk_snapshots")
    op.drop_index("ix_trade_ideas_instr", table_name="trade_ideas")
    op.drop_table("trade_ideas")
    op.drop_column("instruments", "trading_hours")
