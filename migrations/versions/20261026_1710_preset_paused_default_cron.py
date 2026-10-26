"""presets.paused and a schedule for a default preset that has none (#119)

Revision ID: 20261026_1710
Revises: 20261026_1300
Create Date: 2026-10-26 17:10:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261026_1710"
down_revision = "20261026_1300"
branch_labels = None
depends_on = None

DEFAULT_PRESET_CRON = "0 2 * * *"


def upgrade() -> None:
    op.add_column(
        "presets",
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        schema="cryo",
    )
    # The scheduler only fires presets with a cron, so a default preset without one (the
    # bootstrap default-spark-compact ships with NULL) never maintains the tables it catches.
    # Only fill a missing cron; an operator-set schedule is left alone.
    op.execute(
        f"""
        UPDATE cryo.presets
        SET schedule_cron = '{DEFAULT_PRESET_CRON}'
        WHERE is_default = true
          AND (schedule_cron IS NULL OR btrim(schedule_cron) = '')
        """
    )


def downgrade() -> None:
    op.drop_column("presets", "paused", schema="cryo")
