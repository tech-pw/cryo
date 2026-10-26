"""jobs.deferred_since: when the job's current deferral episode began

Revision ID: 20261026_1200
Revises: 20261022_1200

The run finalizer measured a deferred job's grace window from ``finished_at``, which every
re-defer restamps. A job a gate keeps refusing (approval, PII tagging) restarted its own grace
on each wave and held its run open indefinitely (#167). ``deferred_since`` is set on the first
defer and kept until the job is admitted or finishes.

Existing deferred jobs are backfilled from ``finished_at``: the best available estimate, and
it lets runs that are already stuck close on the next finalizer pass.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261026_1200"
down_revision = "20261022_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("deferred_since", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="cryo",
    )
    op.execute(
        "UPDATE cryo.jobs SET deferred_since = finished_at "
        "WHERE status = 'deferred' AND deferred_since IS NULL"
    )


def downgrade() -> None:
    op.drop_column("jobs", "deferred_since", schema="cryo")
