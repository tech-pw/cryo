"""Add cryo.table_query_activity, a day-bucketed per-table query counter.

query_patterns is aggregated per (table, column) -- filter/join/projection counts, not a
query-level counter -- so nothing could answer "how many queries touched this table in the
last 30 days" from it alone. TableStats.query_count_30d had been stuck at 0 for every table
fleet-wide as a result. This table gives both telemetry ingestion paths a place to record a
real per-query count, day-bucketed so a rolling 30-day sum is a plain SUM(...) WHERE day >=
cutoff instead of needing incremental decay logic.

Revision ID: 20260731_0035
Revises: 20260729_0034
Create Date: 2026-07-31 15:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260731_0035"
down_revision = "20260729_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "table_query_activity",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "table_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cryo.tables.id"),
            nullable=False,
        ),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("query_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("table_id", "day", name="uq_table_query_activity_table_day"),
        schema="cryo",
    )
    op.create_index(
        "ix_table_query_activity_table_id_day",
        "table_query_activity",
        ["table_id", "day"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_table_query_activity_table_id_day", table_name="table_query_activity", schema="cryo"
    )
    op.drop_table("table_query_activity", schema="cryo")
