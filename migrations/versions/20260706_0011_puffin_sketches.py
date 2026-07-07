"""Puffin sketches + silent-data-loss event tables.

Adds ``cryo.column_stats_sketch`` and ``cryo.silent_data_loss_events`` to back the
"Zero-Scan Data Quality" (FORMATS '26, arXiv:2605.30308) Puffin feature and the silent
data-loss detector (arXiv:2604.20081). Both tables live in the ``cryo`` schema and use
the same UUID primary-key pattern as the rest of the platform.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260706_0011_puffin"
down_revision = "20260706_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "column_stats_sketch",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "table_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cryo.tables.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("column_name", sa.Text(), nullable=False),
        sa.Column("sketch_type", sa.Text(), nullable=False),
        sa.Column("puffin_path", sa.Text(), nullable=False),
        sa.Column("puffin_offset", sa.BigInteger(), nullable=False),
        sa.Column("puffin_size", sa.Integer(), nullable=False),
        sa.Column("field_type", sa.Text(), nullable=True),
        sa.Column(
            "merged_from",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("scan_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="cryo",
    )
    op.create_index(
        "idx_css_table_column",
        "column_stats_sketch",
        ["table_id", "column_name"],
        schema="cryo",
    )

    op.create_table(
        "silent_data_loss_events",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "table_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cryo.tables.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "detected_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "orphan_data_files",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "orphan_size_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "missing_files",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "root_cause",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'unknown'"),
        ),
        sa.Column(
            "repaired_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "audit_log_id",
            sa.BigInteger(),
            sa.ForeignKey("cryo.audit_log.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="cryo",
    )
    op.create_index(
        "idx_silent_data_loss_table_time",
        "silent_data_loss_events",
        ["table_id", "detected_at"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_silent_data_loss_table_time",
        table_name="silent_data_loss_events",
        schema="cryo",
    )
    op.drop_table("silent_data_loss_events", schema="cryo")
    op.drop_index(
        "idx_css_table_column",
        table_name="column_stats_sketch",
        schema="cryo",
    )
    op.drop_table("column_stats_sketch", schema="cryo")