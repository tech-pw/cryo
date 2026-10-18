"""archive execution: archive jobs and per-partition storage class

Revision ID: 20261018_1200
Revises: 20261017_1200
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261018_1200"
down_revision = "20261017_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "partition_storage_classes",
        sa.Column("table_id", sa.Uuid(), nullable=False),
        sa.Column("partition_key", sa.Text(), nullable=False),
        sa.Column("storage_class", sa.Text(), nullable=False),
        sa.Column("objects", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["table_id"],
            ["cryo.tables.id"],
            name="fk_partition_storage_classes_table_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("table_id", "partition_key", name="pk_partition_storage_classes"),
        schema="cryo",
    )
    op.create_table(
        "archive_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("table_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_class", sa.Text(), nullable=False),
        sa.Column("partition_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("objects_total", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("objects_moved", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("objects_skipped", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("objects_failed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("bytes_moved", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "errors",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["table_id"], ["cryo.tables.id"], name="fk_archive_jobs_table_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_archive_jobs"),
        schema="cryo",
    )
    op.create_index(
        "ix_archive_jobs_table_created",
        "archive_jobs",
        ["table_id", "created_at"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index("ix_archive_jobs_table_created", table_name="archive_jobs", schema="cryo")
    op.drop_table("archive_jobs", schema="cryo")
    op.drop_table("partition_storage_classes", schema="cryo")
