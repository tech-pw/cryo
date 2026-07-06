"""Variance-aware QPP observations (CLOUD 2026, arXiv:2606.03464).

Adds the ``cryo.qpp_observations`` table that backs the Governor's variance-aware
query-performance predictor. Each row is a single historical observation -- features plus
actual runtime -- that the linear-regression trainer consumes to refine the heuristic
baseline over time.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260706_0012"
down_revision = "20260706_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qpp_observations",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "observed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("table_id", UUID(as_uuid=True), nullable=False),
        sa.Column("features", JSONB(), nullable=False),
        sa.Column("p50_ms", sa.Float(), nullable=True),
        sa.Column("p95_ms", sa.Float(), nullable=True),
        sa.Column("p99_ms", sa.Float(), nullable=True),
        sa.Column("actual_p95_ms", sa.Float(), nullable=True),
        sa.Column("cotenant_load_pct", sa.Float(), nullable=True),
        sa.Column(
            "is_cached",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema="cryo",
    )
    op.create_index(
        "idx_qpp_table_time",
        "qpp_observations",
        ["table_id", sa.text("observed_at DESC")],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index("idx_qpp_table_time", table_name="qpp_observations", schema="cryo")
    op.drop_table("qpp_observations", schema="cryo")