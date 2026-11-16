"""Per-table health-grade threshold overrides (#552)

Revision ID: 20261116_1000
Revises: 20261113_1300

Nullable and additive: a NULL column grades the table exactly as before.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261116_1000"
down_revision = "20261113_1300"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tables",
        sa.Column("health_threshold_overrides", postgresql.JSONB(), nullable=True),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("tables", "health_threshold_overrides", schema="cryo")
