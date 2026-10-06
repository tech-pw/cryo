"""Add engine_ref/resolved_at to audit_log for async fallback-dispatch tracking.

Revision ID: 20260811_0039
Revises: 20260804_0038
Create Date: 2026-08-11 21:00:00.000000

_dispatch_fallback_operations used to submit a Spark
fallback job (e.g. optimize_manifests, unsupported by the native engine) and block
run_wave() until it finished -- confirmed live, 2026-08-11: with the fallback
destination's own slot_cap=6 and each Spark submission+wait taking real wall-clock
time, a single 30s wave containing many fallback-needing tables could take minutes,
capping the whole Governor's admission throughput to ~12 jobs/min regardless of the
native engine's own slot_cap=80 -- 7,855 deep-mode jobs queued and barely draining.

The fix makes fallback dispatch fire-and-forget from run_wave()'s perspective: submit,
record status=SUBMITTED with the engine_ref needed to check on it later, and move on.
A separate scheduler-tick sweep resolves SUBMITTED rows to OK/FAILED once the Spark
job reaches a terminal state. engine_ref is the K8s Job name (e.g. cryo-spark-abc123);
resolved_at marks when the sweep last updated a row, so the sweep's own query
(status=SUBMITTED) doesn't repeatedly reconsider rows it just wrote back.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260811_0039"
down_revision = "20260804_0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_log",
        sa.Column("engine_ref", sa.Text(), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "audit_log",
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="cryo",
    )
    # The sweep's own query is `status = 'SUBMITTED'` -- a partial index keeps that
    # cheap regardless of how large audit_log grows, without indexing every row.
    op.execute(
        "CREATE INDEX ix_audit_log_submitted_pending ON cryo.audit_log (id) "
        "WHERE status = 'SUBMITTED'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS cryo.ix_audit_log_submitted_pending")
    op.drop_column("audit_log", "resolved_at", schema="cryo")
    op.drop_column("audit_log", "engine_ref", schema="cryo")
