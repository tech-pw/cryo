"""initial cryo schema

Revision ID: 20260630_0001
Revises:
Create Date: 2026-06-30 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260630_0001"
down_revision = None
branch_labels = None
depends_on = None


def jsonb_default(value: str) -> sa.TextClause:
    return sa.text(f"'{value}'::jsonb")


def uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        nullable=False,
        server_default=sa.text("gen_random_uuid()"),
    )


def timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS cryo")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "sources",
        uuid_pk(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("secret_ref", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        schema="cryo",
    )

    op.create_table(
        "destinations",
        uuid_pk(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("engine", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("secret_ref", sa.Text(), nullable=True),
        sa.Column("offpeak_start", sa.Integer(), nullable=True),
        sa.Column("offpeak_end", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        schema="cryo",
    )

    op.create_table(
        "tables",
        uuid_pk(),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_name", sa.Text(), nullable=False),
        sa.Column("table_name", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.ForeignKeyConstraint(["source_id"], ["cryo.sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "schema_name", "table_name"),
        schema="cryo",
    )

    op.create_table(
        "presets",
        uuid_pk(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("destination_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False, server_default=sa.text("'standard'")),
        sa.Column("operations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=jsonb_default("{}")),
        sa.Column("schedule_cron", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("100")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("guardrails", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=jsonb_default("{}")),
        *timestamps(),
        sa.ForeignKeyConstraint(["destination_id"], ["cryo.destinations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        schema="cryo",
    )

    op.create_table(
        "table_stats",
        sa.Column("table_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("needs_compaction", sa.Boolean(), nullable=False),
        sa.Column("compaction_priority", sa.Text(), nullable=False),
        sa.Column("grade", sa.Text(), nullable=False),
        sa.Column("overall_score", sa.Integer(), nullable=False),
        sa.Column("total_files", sa.BigInteger(), nullable=False),
        sa.Column("small_files_count", sa.BigInteger(), nullable=False),
        sa.Column("total_partitions", sa.Integer(), nullable=True),
        sa.Column("max_files_per_partition", sa.Integer(), nullable=True),
        sa.Column("avg_files_per_partition", sa.Float(), nullable=True),
        sa.Column("partition_skew_ratio", sa.Float(), nullable=True),
        sa.Column("total_records", sa.BigInteger(), nullable=True),
        sa.Column("total_size_gb", sa.Float(), nullable=False),
        sa.Column("avg_file_size_mb", sa.Float(), nullable=True),
        sa.Column("total_snapshots", sa.Integer(), nullable=True),
        sa.Column("total_delete_files", sa.Integer(), nullable=True),
        sa.Column("delete_file_pct", sa.Float(), nullable=True),
        sa.Column("manifest_count", sa.Integer(), nullable=True),
        sa.Column("partition_col", sa.Text(), nullable=True),
        sa.Column("partition_col_type", sa.Text(), nullable=True),
        sa.Column("write_distribution_mode", sa.Text(), nullable=True),
        sa.Column("has_sort_order", sa.Boolean(), nullable=True),
        sa.Column("target_file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("estimated_monthly_s3_cost_usd", sa.Float(), nullable=True),
        sa.Column("query_count_30d", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["table_id"], ["cryo.tables.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("table_id"),
        schema="cryo",
    )

    op.create_table(
        "table_partitions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("table_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("partition_value", sa.Text(), nullable=True),
        sa.Column("partition_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=jsonb_default("{}")),
        sa.Column("partition_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=jsonb_default("[]")),
        sa.Column("partition_spec_id", sa.Integer(), nullable=True),
        sa.Column("partition_path", sa.Text(), nullable=True),
        sa.Column("partition_key", sa.Text(), nullable=True),
        sa.Column("base_column", sa.Text(), nullable=True),
        sa.Column("base_column_type", sa.Text(), nullable=True),
        sa.Column("transform", sa.Text(), nullable=True),
        sa.Column("files_per_partition", sa.Integer(), nullable=True),
        sa.Column("small_files_in_partition_count", sa.Integer(), nullable=True),
        sa.Column("partition_size_gb", sa.Float(), nullable=True),
        sa.Column("compaction_status", sa.Text(), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["table_id"], ["cryo.tables.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        schema="cryo",
    )
    op.create_index("ix_table_partitions_table_scan", "table_partitions", ["table_id", "scan_at"], schema="cryo")
    op.create_index("ix_table_partitions_values_gin", "table_partitions", ["partition_values"], schema="cryo", postgresql_using="gin")

    op.create_table(
        "table_stats_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("table_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("small_files_count", sa.BigInteger(), nullable=True),
        sa.Column("total_files", sa.BigInteger(), nullable=True),
        sa.Column("total_size_gb", sa.Float(), nullable=True),
        sa.Column("grade", sa.Text(), nullable=True),
        sa.Column("overall_score", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["table_id"], ["cryo.tables.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        schema="cryo",
    )
    op.create_index("ix_table_stats_history_table_scan", "table_stats_history", ["table_id", "scan_at"], schema="cryo")

    op.create_table(
        "preset_rules",
        uuid_pk(),
        sa.Column("preset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("predicate", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.ForeignKeyConstraint(["preset_id"], ["cryo.presets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        schema="cryo",
    )

    op.create_table(
        "table_assignments",
        sa.Column("table_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("preset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_by", sa.Text(), nullable=True),
        sa.Column("assigned_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.ForeignKeyConstraint(["preset_id"], ["cryo.presets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["table_id"], ["cryo.tables.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("table_id"),
        schema="cryo",
    )

    op.create_table(
        "runs",
        uuid_pk(),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("preset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trigger", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'running'")),
        sa.Column("deadline_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["preset_id"], ["cryo.presets.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="cryo",
    )

    op.create_table(
        "jobs",
        uuid_pk(),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("table_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("destination_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("plan", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("engine_ref", sa.Text(), nullable=True),
        sa.Column("replica", sa.Text(), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["destination_id"], ["cryo.destinations.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["cryo.runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["table_id"], ["cryo.tables.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="cryo",
    )
    op.create_index("ix_jobs_run_status", "jobs", ["run_id", "status"], schema="cryo")

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("table_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("engine", sa.Text(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["cryo.jobs.id"]),
        sa.ForeignKeyConstraint(["table_id"], ["cryo.tables.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="cryo",
    )
    op.create_index("ix_audit_log_table_ts", "audit_log", ["table_id", "ts"], schema="cryo")


def downgrade() -> None:
    op.drop_index("ix_audit_log_table_ts", table_name="audit_log", schema="cryo")
    op.drop_table("audit_log", schema="cryo")
    op.drop_index("ix_jobs_run_status", table_name="jobs", schema="cryo")
    op.drop_table("jobs", schema="cryo")
    op.drop_table("runs", schema="cryo")
    op.drop_table("table_assignments", schema="cryo")
    op.drop_table("preset_rules", schema="cryo")
    op.drop_index("ix_table_stats_history_table_scan", table_name="table_stats_history", schema="cryo")
    op.drop_table("table_stats_history", schema="cryo")
    op.drop_index("ix_table_partitions_values_gin", table_name="table_partitions", schema="cryo")
    op.drop_index("ix_table_partitions_table_scan", table_name="table_partitions", schema="cryo")
    op.drop_table("table_partitions", schema="cryo")
    op.drop_table("table_stats", schema="cryo")
    op.drop_table("presets", schema="cryo")
    op.drop_table("tables", schema="cryo")
    op.drop_table("destinations", schema="cryo")
    op.drop_table("sources", schema="cryo")
    op.execute("DROP SCHEMA IF EXISTS cryo")
