"""Add runs.scheduled_at for one-off future-scheduled maintenance runs

Revision ID: 20260707_0021
Revises: 20260707_0019_s3_request_cost_p0
Create Date: 2026-07-07 21:00:00.000000

Lets an operator pick a specific future date/time for a maintenance run,
distinct from a Preset's recurring `schedule_cron`. See
docs/ux-improvement-loop.md for the design conversation that motivated this
and cryo/scheduler/service.py for the dispatch step that picks these up.

Additive only: one new nullable column. Existing runs are unaffected --
`scheduled_at IS NULL` means "not a scheduled run" for all of them.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260707_0021"
down_revision = "20260707_0019_s3_request_cost_p0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("runs", "scheduled_at", schema="cryo")
