"""Add partition_coverage_cursor(_scan_at) to table_stats.

Revision ID: 20260920_0050
Revises: 20260920_0049
Create Date: 2026-09-20 00:00:01.000000

Recovery plan §3, 2026-09-20: update_partition_benefit_scores used a bare `.limit(10000)`
safety cap with no ORDER BY -- for any table over 10k partitions (confirmed live: a
397k-partition outlier), a *different arbitrary* subset could get scored each sweep, with no
coverage tracking and no guarantee the remainder is ever reached. These two columns hold a
resumable keyset cursor (last-scored TablePartition.id) plus the scan_at it was computed
against, so a table's oversized partition set makes real forward progress across sweeps
instead of the same fraction being rescored forever.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260920_0050"
down_revision = "20260920_0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "table_stats",
        sa.Column("partition_coverage_cursor", sa.BigInteger(), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "table_stats",
        sa.Column(
            "partition_coverage_cursor_scan_at", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("table_stats", "partition_coverage_cursor_scan_at", schema="cryo")
    op.drop_column("table_stats", "partition_coverage_cursor", schema="cryo")
