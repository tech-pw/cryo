"""Add cryo.job_steps ledger and jobs.current_step_index.

Revision ID: 20260727_0033
Revises: 20260724_0032
Create Date: 2026-07-27 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260727_0033"
down_revision = "20260724_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("current_step_index", sa.Integer(), nullable=True),
        schema="cryo",
    )
    op.create_table(
        "job_steps",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cryo.jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("operation_type", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'started'"),
        ),
        sa.Column("engine_ref", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("job_id", "step_index", name="uq_job_steps_job_step"),
        schema="cryo",
    )
    op.create_index(
        "ix_job_steps_job_status",
        "job_steps",
        ["job_id", "status"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index("ix_job_steps_job_status", table_name="job_steps", schema="cryo")
    op.drop_table("job_steps", schema="cryo")
    op.drop_column("jobs", "current_step_index", schema="cryo")
