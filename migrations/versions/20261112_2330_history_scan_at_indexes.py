"""Indexes on scan_at for the stats and cost history tables

Revision ID: 20261112_2330
Revises: 20261109_1200

The Home drift chart, the Cost trend and the vitals small-file trend read the last N days of
history across the fleet (#494). The only history index leads with table_id, so a scan_at
range could not use it and every load ranked the whole history.
"""

from __future__ import annotations

from alembic import op

revision = "20261112_2330"
down_revision = "20261109_1200"
branch_labels = None
depends_on = None

_INDEXES = (
    ("ix_table_stats_history_scan_at", "table_stats_history"),
    ("ix_table_cost_history_scan_at", "table_cost_history"),
)


def upgrade() -> None:
    # CONCURRENTLY: the scanner writes history rows all night; a plain CREATE INDEX blocks it.
    # A failed earlier build leaves an INVALID index that IF NOT EXISTS would keep.
    with op.get_context().autocommit_block():
        for name, table in _INDEXES:
            op.drop_index(
                name, table_name=table, schema="cryo", postgresql_concurrently=True, if_exists=True
            )
            op.create_index(
                name,
                table,
                ["scan_at"],
                schema="cryo",
                postgresql_concurrently=True,
                if_not_exists=True,
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, table in _INDEXES:
            op.drop_index(
                name, table_name=table, schema="cryo", postgresql_concurrently=True, if_exists=True
            )
