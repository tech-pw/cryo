"""AutoComp MOOP traits (SIGMOD 2025 §A2): file entropy + normalized trait/score snapshot.

Additive columns on cryo.table_partitions so update_partition_benefit_scores can persist the
multi-objective scalarization inputs alongside the existing benefit_score, for the Smart
Maintenance UI and fleet-budget admission gate to read without recomputing.

Revision ID: 20260720_0027
Revises: 20260709_0026
Create Date: 2026-07-20 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260720_0027"
down_revision = "20260709_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "table_partitions",
        sa.Column("file_entropy", sa.Float(), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "table_partitions",
        sa.Column("autocomp_traits", JSONB(), nullable=True),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("table_partitions", "autocomp_traits", schema="cryo")
    op.drop_column("table_partitions", "file_entropy", schema="cryo")
