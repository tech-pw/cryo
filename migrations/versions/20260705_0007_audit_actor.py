"""Add actor column to audit_log for ad-hoc run attribution."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260705_0007"
down_revision = "20260705_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_log",
        sa.Column("actor", sa.Text(), nullable=True),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("audit_log", "actor", schema="cryo")
