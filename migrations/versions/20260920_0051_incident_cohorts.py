"""Add incident_cohorts / incident_cohort_members tables.

Revision ID: 20260920_0051
Revises: 20260920_0050
Create Date: 2026-09-20 00:00:02.000000

Recovery plan §4/§5, 2026-09-20: persists production-incident run cohorts (e.g. the Sep 17-18
zombie-run investigation) as durable, named, re-verifiable records instead of one-off findings
that only ever existed in an investigation session's scrollback. See cryo/db/models.py's
IncidentCohort/IncidentCohortMember docstrings for the full design rationale -- this migration
intentionally creates storage only, no automated reconciliation trigger.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260920_0051"
down_revision = "20260920_0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incident_cohorts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("evidence_query", sa.Text(), nullable=False),
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
    op.create_table(
        "incident_cohort_members",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "cohort_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cryo.incident_cohorts.id"),
            nullable=False,
        ),
        sa.Column(
            "run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cryo.runs.id"), nullable=False
        ),
        sa.Column(
            "reconciliation_status", sa.Text(), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("reconciled_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
        "ux_incident_cohort_members_cohort_run",
        "incident_cohort_members",
        ["cohort_id", "run_id"],
        unique=True,
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index(
        "ux_incident_cohort_members_cohort_run",
        table_name="incident_cohort_members",
        schema="cryo",
    )
    op.drop_table("incident_cohort_members", schema="cryo")
    op.drop_table("incident_cohorts", schema="cryo")
