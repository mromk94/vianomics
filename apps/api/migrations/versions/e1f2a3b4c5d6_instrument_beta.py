"""instrument beta — V1 Step 6B CAPM discount rate input

The discount rate is Risk-Free + Beta × ERP; beta was hardcoded 1.0.
Persist the computed market beta (weekly returns vs ^GSPC, ~2y OLS
slope) so CAPM uses a real per-instrument estimate — auditable via
beta_asof and recomputable on demand.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
"""

from alembic import op
import sqlalchemy as sa

revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("instruments",
                  sa.Column("beta", sa.Numeric(10, 4), nullable=True))
    op.add_column("instruments",
                  sa.Column("beta_asof", sa.DateTime(timezone=True),
                            nullable=True))


def downgrade() -> None:
    op.drop_column("instruments", "beta_asof")
    op.drop_column("instruments", "beta")
