"""market_quotes — latest quote per (source, symbol); the live-tape
primitive fed by MT4 EA pushes and provider quote refreshes.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2025-10-04
"""
from alembic import op
import sqlalchemy as sa

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_quotes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("instrument_id", sa.String(),
                  sa.ForeignKey("instruments.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("bid", sa.Numeric(24, 8), nullable=True),
        sa.Column("ask", sa.Numeric(24, 8), nullable=True),
        sa.Column("mid", sa.Numeric(24, 8), nullable=True),
        sa.Column("day_open", sa.Numeric(24, 8), nullable=True),
        sa.Column("prev_close", sa.Numeric(24, 8), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.UniqueConstraint("source", "symbol"),
    )
    op.create_index("ix_quotes_symbol", "market_quotes", ["symbol"])
    op.create_index("ix_market_quotes_instrument_id", "market_quotes",
                    ["instrument_id"])
    op.create_index("ix_market_quotes_ts", "market_quotes", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_market_quotes_ts", table_name="market_quotes")
    op.drop_index("ix_market_quotes_instrument_id",
                  table_name="market_quotes")
    op.drop_index("ix_quotes_symbol", table_name="market_quotes")
    op.drop_table("market_quotes")
