"""maintenance exclusion expiry, for time-boxed per-table holds

Revision ID: 20261018_1300
Revises: 20261018_1200
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261018_1300"
down_revision = "20261018_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "maintenance_exclusions",
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("maintenance_exclusions", "expires_at", schema="cryo")
