"""Add native_dispatches, job.dispatch_id, checkpoint table_id.

Revision ID: 20260720_0027
Revises: 20260709_0026
Create Date: 2026-07-20 16:51:00.000000

Native maintenance Job dispatch (create-on-admit):
- cryo.native_dispatches tracks one K8s Job that may run 1..M table jobs
- cryo.jobs.dispatch_id links each MaintenanceJob to its dispatch
- cryo.chunk_checkpoints.table_id disambiguates chunks when multiple tables
  share a run (required for multi-table serial pods)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260720_0027"
down_revision = "20260709_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "native_dispatches",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("destination_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("k8s_job_name", sa.Text(), nullable=True),
        sa.Column(
            "k8s_namespace",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'default'"),
        ),
        sa.Column("cpu_milli", sa.Integer(), nullable=False),
        sa.Column("mem_mib", sa.Integer(), nullable=False),
        sa.Column(
            "table_job_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("dedicated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.ForeignKeyConstraint(["run_id"], ["cryo.runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["destination_id"], ["cryo.destinations.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="cryo",
    )
    op.create_index(
        "ix_native_dispatches_run",
        "native_dispatches",
        ["run_id"],
        schema="cryo",
    )
    op.create_index(
        "ix_native_dispatches_status",
        "native_dispatches",
        ["status"],
        schema="cryo",
    )
    op.create_index(
        "ix_native_dispatches_k8s_job",
        "native_dispatches",
        ["k8s_job_name"],
        schema="cryo",
        unique=False,
    )

    op.add_column(
        "jobs",
        sa.Column("dispatch_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="cryo",
    )
    op.create_foreign_key(
        "fk_jobs_dispatch_id",
        "jobs",
        "native_dispatches",
        ["dispatch_id"],
        ["id"],
        source_schema="cryo",
        referent_schema="cryo",
        ondelete="SET NULL",
    )
    op.create_index("ix_jobs_dispatch_id", "jobs", ["dispatch_id"], schema="cryo")

    # Checkpoint: add nullable table_id, replace unique constraint.
    op.add_column(
        "chunk_checkpoints",
        sa.Column("table_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="cryo",
    )
    op.create_foreign_key(
        "fk_chunk_checkpoints_table_id",
        "chunk_checkpoints",
        "tables",
        ["table_id"],
        ["id"],
        source_schema="cryo",
        referent_schema="cryo",
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "uq_chunk_checkpoint_run_chunk",
        "chunk_checkpoints",
        schema="cryo",
        type_="unique",
    )
    # Partial unique: when table_id is set, uniqueness is (run_id, table_id, chunk_index).
    # When table_id is NULL (legacy), keep (run_id, chunk_index) uniqueness via index.
    op.create_index(
        "uq_chunk_checkpoint_run_table_chunk",
        "chunk_checkpoints",
        ["run_id", "table_id", "chunk_index"],
        unique=True,
        schema="cryo",
        postgresql_where=sa.text("table_id IS NOT NULL"),
    )
    op.create_index(
        "uq_chunk_checkpoint_run_chunk_legacy",
        "chunk_checkpoints",
        ["run_id", "chunk_index"],
        unique=True,
        schema="cryo",
        postgresql_where=sa.text("table_id IS NULL"),
    )
    op.create_index(
        "ix_chunk_checkpoints_table",
        "chunk_checkpoints",
        ["table_id"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index("ix_chunk_checkpoints_table", table_name="chunk_checkpoints", schema="cryo")
    op.drop_index(
        "uq_chunk_checkpoint_run_chunk_legacy",
        table_name="chunk_checkpoints",
        schema="cryo",
    )
    op.drop_index(
        "uq_chunk_checkpoint_run_table_chunk",
        table_name="chunk_checkpoints",
        schema="cryo",
    )
    op.create_unique_constraint(
        "uq_chunk_checkpoint_run_chunk",
        "chunk_checkpoints",
        ["run_id", "chunk_index"],
        schema="cryo",
    )
    op.drop_constraint(
        "fk_chunk_checkpoints_table_id",
        "chunk_checkpoints",
        schema="cryo",
        type_="foreignkey",
    )
    op.drop_column("chunk_checkpoints", "table_id", schema="cryo")

    op.drop_index("ix_jobs_dispatch_id", table_name="jobs", schema="cryo")
    op.drop_constraint("fk_jobs_dispatch_id", "jobs", schema="cryo", type_="foreignkey")
    op.drop_column("jobs", "dispatch_id", schema="cryo")

    op.drop_index("ix_native_dispatches_k8s_job", table_name="native_dispatches", schema="cryo")
    op.drop_index("ix_native_dispatches_status", table_name="native_dispatches", schema="cryo")
    op.drop_index("ix_native_dispatches_run", table_name="native_dispatches", schema="cryo")
    op.drop_table("native_dispatches", schema="cryo")
