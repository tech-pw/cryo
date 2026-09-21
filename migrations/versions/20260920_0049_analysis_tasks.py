"""Add analysis_tasks: durable, resumable heavy-analysis work units.

Revision ID: 20260920_0049
Revises: 20260908_0048
Create Date: 2026-09-20 00:00:00.000000

Recovery plan §3, 2026-09-20 (docs/CRYO_PERMANENT_RECOVERY_PLAN_2026-09-20.md): the scheduler
ran a multi-hour, ~13k-table benefit-score/realized-value sweep as an in-process thread with no
persisted progress -- a restart (crash, OOM, redeploy) threw away all progress and restarted the
entire sweep from table 1, and the crash-loop was actively costing scheduling throughput, not
just restarting harmlessly (confirmed live: north-star GB-freed/day fell during a prior
crash-loop incident of the same shape). This table lets heavy analysis run as a short-lived,
separately-resourced K8s Job with real resumable state, while the scheduler stays a lightweight
coordinator. See cryo/db/models.py::AnalysisTask for the full field-by-field rationale.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260920_0049"
down_revision = "20260908_0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analysis_tasks",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("task_type", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("generation", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "due_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("input_watermark", sa.Text()),
        sa.Column("cursor", sa.Text()),
        sa.Column("lease_owner", sa.Text()),
        sa.Column("lease_generation", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("lease_until", sa.TIMESTAMP(timezone=True)),
        sa.Column("heartbeat_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("external_job_ref", sa.Text()),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("processed_count", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("failure_reason", sa.Text()),
        sa.Column("next_retry_at", sa.TIMESTAMP(timezone=True)),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="cryo",
    )
    op.create_index(
        "ix_analysis_tasks_due_at", "analysis_tasks", ["due_at"], schema="cryo"
    )
    # One active logical task per (task_type, scope) -- see AnalysisTask's own docstring for
    # why this is the mechanism that makes a scheduler restart reconcile the existing row
    # instead of racing a duplicate.
    op.create_index(
        "ux_analysis_tasks_active_type_scope",
        "analysis_tasks",
        ["task_type", "scope"],
        unique=True,
        schema="cryo",
        postgresql_where=sa.text("status IN ('pending', 'leased', 'running')"),
    )


def downgrade() -> None:
    op.drop_index(
        "ux_analysis_tasks_active_type_scope", table_name="analysis_tasks", schema="cryo"
    )
    op.drop_index("ix_analysis_tasks_due_at", table_name="analysis_tasks", schema="cryo")
    op.drop_table("analysis_tasks", schema="cryo")
