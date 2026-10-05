"""pyramid_trades.direction — long|short.

The doc's "for shorts, direction must be reversed" needs a durable
direction on the position record: stops trail above price and
ratchet down for shorts, targets walk below entry, P&L signs flip.
Existing rows are long.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
"""
from alembic import op
import sqlalchemy as sa

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pyramid_trades",
        sa.Column("direction", sa.String(), nullable=False,
                  server_default="long"))


def downgrade() -> None:
    op.drop_column("pyramid_trades", "direction")
