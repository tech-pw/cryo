"""Add table_similarity_pairs and table_similarity_clusters for the table-dedup pipeline.

Revision ID: 20260813_0040
Revises: 20260811_0039
Create Date: 2026-08-13 12:00:00.000000

Backs the table-level duplicate/near-duplicate detection pipeline described in
docs/TABLE_DEDUPLICATION_DESIGN.md, built at the operator's request after a naming-
convention-only scan found 557 tables / ~18TB matching backup/temp/copy/versioned
patterns fleet-wide, with concrete parent-copy pairs up to ~10TB combined.

table_similarity_pairs holds one row per Stage-1 candidate pair (never the full n^2
cross product -- Stage 1 blocking on schema+size keeps this small) with the full
scoring evidence trail, so every recommendation shown to an operator can explain
itself (design doc SS7/SS9: no bare numeric score for something that can end in a
DROP TABLE, given the gold_gst_slabs incident earlier this session).

table_similarity_clusters holds the connected-components grouping computed from
pairs above threshold, replaced wholesale each pipeline run (generation-numbered)
rather than incrementally patched, since cluster membership can legitimately change
as new pairs get scored.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260813_0040"
down_revision = "20260811_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "table_similarity_pairs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "table_a_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cryo.tables.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "table_b_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cryo.tables.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.Text(), nullable=False, server_default="blocked"),
        sa.Column("similarity_score", sa.Float(), nullable=True),
        sa.Column("schema_similarity", sa.Float(), nullable=True),
        sa.Column("stats_similarity", sa.Float(), nullable=True),
        sa.Column("row_jaccard_estimate", sa.Float(), nullable=True),
        sa.Column("naming_pattern_boost", sa.Float(), nullable=True),
        sa.Column("file_overlap_detected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("verified_relationship", sa.Text(), nullable=True),
        sa.Column("exact_row_overlap_pct", sa.Float(), nullable=True),
        sa.Column(
            "score_components",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "computed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("table_a_id", "table_b_id", name="uq_table_similarity_pair"),
        schema="cryo",
    )
    op.create_index(
        "ix_table_similarity_pairs_table_a", "table_similarity_pairs", ["table_a_id"], schema="cryo"
    )
    op.create_index(
        "ix_table_similarity_pairs_table_b", "table_similarity_pairs", ["table_b_id"], schema="cryo"
    )
    op.create_index(
        "ix_table_similarity_pairs_score",
        "table_similarity_pairs",
        ["similarity_score"],
        schema="cryo",
    )

    op.create_table(
        "table_similarity_clusters",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "table_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cryo.tables.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text(), nullable=False, server_default="drop_candidate"),
        sa.Column("keep_score", sa.Float(), nullable=True),
        sa.Column("keep_reason", sa.Text(), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "computed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="cryo",
    )
    op.create_index(
        "ix_table_similarity_clusters_cluster",
        "table_similarity_clusters",
        ["cluster_id"],
        schema="cryo",
    )
    op.create_index(
        "ix_table_similarity_clusters_table",
        "table_similarity_clusters",
        ["table_id"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_table_similarity_clusters_table", table_name="table_similarity_clusters", schema="cryo"
    )
    op.drop_index(
        "ix_table_similarity_clusters_cluster", table_name="table_similarity_clusters", schema="cryo"
    )
    op.drop_table("table_similarity_clusters", schema="cryo")

    op.drop_index(
        "ix_table_similarity_pairs_score", table_name="table_similarity_pairs", schema="cryo"
    )
    op.drop_index(
        "ix_table_similarity_pairs_table_b", table_name="table_similarity_pairs", schema="cryo"
    )
    op.drop_index(
        "ix_table_similarity_pairs_table_a", table_name="table_similarity_pairs", schema="cryo"
    )
    op.drop_table("table_similarity_pairs", schema="cryo")
