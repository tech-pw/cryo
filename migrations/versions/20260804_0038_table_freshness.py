"""Promote the latest Iceberg snapshot commit timestamp for indexed freshness queries.

Revision ID: 20260804_0038
Revises: 20260804_0037
Create Date: 2026-08-04 16:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260804_0038"
down_revision = "20260804_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "table_stats",
        sa.Column("last_snapshot_committed_at", sa.DateTime(timezone=True), nullable=True),
        schema="cryo",
    )
    # pg_input_is_valid() is PostgreSQL 16+; the dev database is 14. A session-scoped try-cast
    # leaves unparseable values NULL the same way on every version.
    op.execute(
        """
        CREATE FUNCTION pg_temp.cryo_try_timestamptz(value text) RETURNS timestamptz
        LANGUAGE plpgsql AS $$
        BEGIN
            RETURN value::timestamptz;
        EXCEPTION WHEN others THEN
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        UPDATE cryo.table_stats
        SET last_snapshot_committed_at =
            pg_temp.cryo_try_timestamptz(raw ->> 'last_snapshot_committed_at')
        WHERE raw ->> 'last_snapshot_committed_at' IS NOT NULL
        """
    )
    op.execute("DROP FUNCTION pg_temp.cryo_try_timestamptz(text)")
    op.create_index(
        "ix_table_stats_last_snapshot_committed_at",
        "table_stats",
        ["last_snapshot_committed_at"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_table_stats_last_snapshot_committed_at",
        table_name="table_stats",
        schema="cryo",
    )
    op.drop_column("table_stats", "last_snapshot_committed_at", schema="cryo")
