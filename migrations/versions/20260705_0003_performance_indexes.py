"""add performance indexes and constraints

Revision ID: 20260705_0003
Revises: 20260705_0002
Create Date: 2026-07-05 11:23:00.000000

This migration originally duplicated or referenced non-existent schema in six places (all fixed
here rather than left broken, since they'd fail on any fresh install, not just this one):
  1. ix_query_logs_executed_at / ix_query_logs_table_id already exist from
     20260703_0007_query_telemetry.py — removed the duplicate creation.
  2. A GIN index on query_logs."tables_accessed" — that column doesn't exist (QueryLog has a
     single table_id FK, already indexed) — removed, nothing real to index.
  3. audit_log indexes referenced table "audit_logs" (plural) — the real table is "audit_log".
  4. That same index used a "started_at" column that doesn't exist on AuditLog (the column is
     "ts"), and duplicated the model's own ix_audit_log_table_ts composite index — dropped rather
     than fixed, since it would have been redundant either way.
  5. query_patterns indexed a "query_count" column that doesn't exist — repointed to the real
     ranking signal, filter_count.
  6. table_partitions.compaction_benefit_score / query_frequency (+ their index) already exist
     from 20260703_0008_autocomp_ratio_tracking.py (query_frequency is Float there, not the
     Integer this migration tried to add) — removed the duplicate add_column/index.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260705_0003"
down_revision = "20260705_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add indexes for query performance dashboard.
    # ix_query_logs_executed_at / ix_query_logs_table_id are NOT created here — see module
    # docstring point 1.
    op.create_index(
        op.f("ix_query_logs_engine"),
        "query_logs",
        ["engine"],
        schema="cryo",
    )

    # Add index for table stats history (health trends). ix_table_stats_history_table_scan
    # (table_id, scan_at) already exists on the model itself — only the scan_at-alone index,
    # which serves a genuinely different query shape, is added here.
    op.create_index(
        op.f("ix_table_stats_history_scan_at"),
        "table_stats_history",
        ["scan_at"],
        schema="cryo",
    )

    # Add index for audit log (job detail page). See module docstring points 3-4.
    op.create_index(
        op.f("ix_audit_log_job_id"),
        "audit_log",
        ["job_id"],
        schema="cryo",
    )

    # Add index for query patterns (partition evolution). See module docstring point 5.
    op.create_index(
        op.f("ix_query_patterns_table_id_filter_count"),
        "query_patterns",
        ["table_id", "filter_count"],
        schema="cryo",
    )

    # table_partitions.compaction_benefit_score / query_frequency and their index already exist
    # from 20260703_0008_autocomp_ratio_tracking.py — see module docstring point 6.


def downgrade() -> None:
    op.drop_index(op.f("ix_query_patterns_table_id_filter_count"), table_name="query_patterns", schema="cryo")
    op.drop_index(op.f("ix_audit_log_job_id"), table_name="audit_log", schema="cryo")
    op.drop_index(op.f("ix_table_stats_history_scan_at"), table_name="table_stats_history", schema="cryo")
    op.drop_index(op.f("ix_query_logs_engine"), table_name="query_logs", schema="cryo")
