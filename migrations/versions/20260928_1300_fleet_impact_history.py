"""Add cryo.fleet_impact_history.

A persisted, one-row-per-day record of Cryo's real fleet-wide impact -- bytes reclaimed,
tables touched, measurement coverage, job success/failure, fleet size/cost. Written by
cryo.reporting.impact_history.record_daily_impact whenever the daily report runs.

Built after repeatedly re-deriving the same numbers by hand from raw AuditLog/TableStats
queries for a management report, with no durable record of what was reported on any given
day. This closes that gap -- trend queries ("how much did Cryo reclaim this month") become
a straight SELECT over a small table instead of a fresh multi-table scan every time.

Revision ID: 20260928_1300
Revises: 20260924_1800
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260928_1300"
down_revision = "20260924_1800"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fleet_impact_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("bytes_freed", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("measurement_state", sa.Text(), nullable=False),
        sa.Column("actions_succeeded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tables_touched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actions_with_bytes_evidence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actions_without_bytes_evidence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("optimize_files_in", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("optimize_files_out", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("active_tables", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tables_grade_d_or_f", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_size_tb", sa.Float(), nullable=True),
        sa.Column("total_monthly_cost_usd", sa.Float(), nullable=True),
        sa.Column("projected_savings_usd_month", sa.Float(), nullable=True),
        sa.Column("jobs_succeeded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jobs_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("infra_cost_usd", sa.Float(), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint("report_date", name="uq_fleet_impact_history_report_date"),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_table("fleet_impact_history", schema="cryo")
