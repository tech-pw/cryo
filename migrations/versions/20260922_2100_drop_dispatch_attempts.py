"""Drop jobs.dispatch_attempts.

Revision ID: 20260922_2100
Revises: 20260922_0055

Added earlier the same day to bound a retry loop in reconcile_stuck_admitted_jobs, which had
to guess whether an ADMITTED job had been dropped by the at-most-once claim handoff or was
simply queued behind more work than the fleet could drain. Admitted jobs now travel over a
Redis Streams consumer group, so a dropped claim is redelivered by the queue itself and the
remaining reconcile case ("the queue has no record of this job at all") is an exact test
rather than an inference. With nothing left to guess, there is no retry loop to bound.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260922_2100"
down_revision = "20260922_0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("jobs", "dispatch_attempts", schema="cryo")


def downgrade() -> None:
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
