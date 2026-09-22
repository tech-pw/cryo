"""Add dispatch_attempts to jobs.

Revision ID: 20260922_0055
Revises: 20260920_0054

reconcile_stuck_admitted_jobs previously failed every ADMITTED job that passed a 15-minute
grace period without an engine_ref. Age plus a missing engine_ref does not identify a dropped
dispatch, though -- it equally describes a job queued behind more work than the worker fleet
can drain in 15 minutes. Live 2026-09-21: 5,952 of one run's 6,117 jobs were recorded as
`stuck_admitted_no_dispatch` while workers were healthy and still draining the same claim
list, which is most of the fleet's entire recorded failure volume.

The reaper now checks claim-list membership to tell a genuinely dropped job from one that is
merely waiting, and returns dropped jobs to QUEUED instead of failing them. dispatch_attempts
bounds that retry loop (cryo.config.max_dispatch_attempts) so a job that keeps getting
dropped still reaches a terminal state rather than cycling forever.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260922_0055"
down_revision = "20260920_0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "dispatch_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("jobs", "dispatch_attempts", schema="cryo")
