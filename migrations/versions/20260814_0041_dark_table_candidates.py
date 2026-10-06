"""Add dark_table_candidates for query-evidence-driven dark/zombie table detection.

Revision ID: 20260814_0041
Revises: 20260813_0040
Create Date: 2026-08-14 15:00:00.000000

Built after mining trino_logs.trino_queries (Trino's own query-completion event log,
already ingested via cryo.telemetry.iceberg_source) alongside cryo.table_stats: 801
active tables, 66TB, ~$1,622/month estimated S3 cost, with zero queries in the last
30 days -- roughly 30% of the fleet's total estimated storage cost sitting in tables
nobody has read. TableStats.query_count_30d already tracks a single rolling window;
this table adds the *sustained* history a real archival decision needs -- a table
with 0 queries in one 30-day snapshot could just be between quarterly reports, so
detection requires several consecutive zero-query windows (see
detect_dark_table_candidates's docstring) before a table is surfaced, and even then
this is detection/surfacing only. No table is deleted or archived by this migration
or the detection job it backs -- status starts at 'candidate' and only ever moves to
'archived' via an explicit, separate, opt-in action (not built yet, deliberately --
"test -> adopt & evolve", same rollout discipline as every other autonomy feature in
this system).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260814_0041"
down_revision = "20260813_0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dark_table_candidates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "table_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cryo.tables.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("consecutive_zero_query_windows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_zero_query_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("total_size_gb", sa.Float(), nullable=False),
        sa.Column("estimated_monthly_cost_usd", sa.Float(), nullable=True),
        # 'candidate' (detected, awaiting review) -> 'dismissed' (operator confirmed it's fine,
        # e.g. a quarterly-report table) or 'archived' (an explicit, separate action moved the
        # data to cold storage / deleted it). No code path in this migration or the detection
        # job ever writes 'archived' -- reserved for a future opt-in action.
        sa.Column("status", sa.Text(), nullable=False, server_default="candidate"),
        sa.Column("dismissed_by", sa.Text(), nullable=True),
        sa.Column("dismissed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("dismissed_reason", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("table_id", name="uq_dark_table_candidates_table"),
        schema="cryo",
    )
    op.create_index(
        "ix_dark_table_candidates_status",
        "dark_table_candidates",
        ["status"],
        schema="cryo",
    )
    op.create_index(
        "ix_dark_table_candidates_size",
        "dark_table_candidates",
        ["total_size_gb"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index("ix_dark_table_candidates_size", table_name="dark_table_candidates", schema="cryo")
    op.drop_index(
        "ix_dark_table_candidates_status", table_name="dark_table_candidates", schema="cryo"
    )
    op.drop_table("dark_table_candidates", schema="cryo")
