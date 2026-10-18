"""index jobs (table_id, finished_at) for per-table last-run lookups

Revision ID: 20261018_1200
Revises: 20261017_1200

The Tables page resolves each row's latest maintenance job, and its triage filters
("failed last run") compile to a correlated subquery ordered by finished_at DESC NULLS FIRST.
A backward scan of (table_id, finished_at) answers that with one index entry per table;
without it every lookup is a sequential scan of jobs.
"""

from __future__ import annotations

from alembic import op

revision = "20261018_1200"
down_revision = "20261017_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CONCURRENTLY: jobs is written by every worker; a plain CREATE INDEX blocks those writes.
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_jobs_table_finished",
            "jobs",
            ["table_id", "finished_at"],
            schema="cryo",
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_jobs_table_finished",
            table_name="jobs",
            schema="cryo",
            postgresql_concurrently=True,
            if_exists=True,
        )
