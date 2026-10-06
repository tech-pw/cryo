"""Add admitted_at to jobs.

Revision ID: 20260908_0048
Revises: 20260908_0047

Root-cause fix for the recurring stuck_runs/queue_growth instability investigated 2026-09-08:
the Worker's admit handoff (RedisMaintenanceQueue.admit -> rpush cryo:admitted, claim_admitted
-> lpop) is at-most-once with no ack or visibility timeout. A worker that claims a job_id and
dies before reaching mark_running() drops it from the Redis list forever -- the DB row stays
ADMITTED, with no engine_ref and no owner, invisible to every existing reconciliation pass
(reconcile_queued_jobs/reconcile_deferred_jobs/reconcile_stale_running_jobs all key off
QUEUED/DEFERRED/RUNNING, never ADMITTED). Live-confirmed: jobs stuck exactly
this way, all on one Spark destination, spanning many hours --
correlated with governor/worker pod restarts across the day's deploys.

Neither started_at (set only on RUNNING) nor finished_at (cleared, not set, on ADMITTED) can
tell "just admitted this wave" from "admitted hours ago and never dispatched." admitted_at,
stamped by mark_status() the instant a job transitions to ADMITTED, gives
reconcile_stuck_admitted_jobs (cryo/queue/reconcile.py) the age signal it needs to safely
re-queue or fail these without risking a job that was admitted seconds ago.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0048"
down_revision = "20260908_0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("admitted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("jobs", "admitted_at", schema="cryo")
