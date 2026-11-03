"""Optional continuous (drift-triggered) block on presets (#77)

Revision ID: 20261103_1510
Revises: 20261103_1500

NULL keeps today's behaviour: the preset only runs on its cron.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261103_1510"
down_revision = "20261103_1500"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "presets",
        sa.Column("continuous", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("presets", "continuous", schema="cryo")
