"""Add chunk_index to jobs + ChunkCheckpoint table for per-chunk checkpointing

Revision ID: 20260708_chunk_checkpointing
Revises: 20260707_0021
Create Date: 2026-07-08 15:30:00.000000

Add chunk_index to MaintenanceJob so each chunk in a multi-chunk native plan
can be dispatched and tracked independently. Create ChunkCheckpoint to record
which chunks have already committed, enabling resume after mid-run failure without
re-running already-committed chunks.

chunk_index is nullable: NULL = legacy single-job-per-run (bundled execution),
integer = chunk N of a per-chunk plan. The planner always assigns chunk_index
in ascending order matching the planner's greedy_pack order.

The PARTIAL JobStatus variant (added to the enum in the same move) is a new
terminal state for runs where some chunks succeeded and some failed -- distinct
from FAILED where no chunks succeeded.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260708_chunk_checkpointing"
down_revision = "20260707_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("chunk_index", sa.Integer(), nullable=True),
        schema="cryo",
    )
    op.create_index(
        "ix_jobs_run_chunk",
        "jobs",
        ["run_id", "chunk_index"],
        schema="cryo",
    )
    op.create_index(
        "ix_jobs_chunk_index",
        "jobs",
        ["chunk_index"],
        schema="cryo",
        unique=False,
        postgresql_where=sa.text("chunk_index IS NOT NULL"),
    )

    op.create_table(
        "chunk_checkpoints",
        sa.Column("id", sa.dialects.postgresql.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("run_id", sa.dialects.postgresql.UUID(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("operation_type", sa.Text(), nullable=False),
        sa.Column("partition_summary", sa.Text(), nullable=True),
        sa.Column("input_files", sa.Integer(), nullable=True),
        sa.Column("output_files", sa.Integer(), nullable=True),
        sa.Column("bytes_removed", sa.BigInteger(), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("committed_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("raw_result", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["cryo.runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "chunk_index", name="uq_chunk_checkpoint_run_chunk"),
        schema="cryo",
    )
    op.create_index("ix_chunk_checkpoints_run", "chunk_checkpoints", ["run_id"], schema="cryo")


def downgrade() -> None:
    op.drop_table("chunk_checkpoints", schema="cryo")
    op.drop_index("ix_jobs_chunk_index", table_name="jobs", schema="cryo")
    op.drop_index("ix_jobs_run_chunk", table_name="jobs", schema="cryo")
    op.drop_column("jobs", "chunk_index", schema="cryo")
