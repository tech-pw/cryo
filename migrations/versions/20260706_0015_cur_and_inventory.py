"""CUR ingestion + S3 Inventory tables for real per-bucket / per-table cost attribution.

Adds ``cryo.aws_cost_insight_configs`` (per-source config for the free CUR + Inventory path),
``cryo.aws_cur_line_items`` (normalized CUR rows, indexed on ``(source_id, usage_date)`` and
``(bucket, usage_date)``), and ``cryo.aws_inventory_objects`` (per-object Inventory rows with
denormalized ``table_prefix`` for cheap per-table attribution).

These tables are deliberately separate from ``cryo.aws_integrations``: the existing AWS
integration is the *free-tier CloudWatch + Pricing List API* path with hard policy guarantees;
CUR + Inventory are a different access pattern (delivered report files, billing-admin setup)
and live behind a separate config row. See ``cryo/integrations/aws_cost_insights.py``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260706_0015"
down_revision = "20260706_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "aws_cost_insight_configs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "source_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cryo.sources.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("cur_bucket", sa.Text(), nullable=True),
        sa.Column("cur_prefix", sa.Text(), nullable=True),
        sa.Column("cur_report_name", sa.Text(), nullable=True),
        sa.Column("fixture_path", sa.Text(), nullable=True),
        sa.Column("inventory_bucket", sa.Text(), nullable=True),
        sa.Column("inventory_prefix", sa.Text(), nullable=True),
        sa.Column(
            "inventory_format",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'CSV'"),
        ),
        sa.Column("access_key_id", sa.Text(), nullable=True),
        sa.Column("secret_ref", sa.Text(), nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("last_ingested_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_ingested_through", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_ingest_ok", sa.Boolean(), nullable=True),
        sa.Column("last_ingest_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema="cryo",
    )

    op.create_table(
        "aws_cur_line_items",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "source_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cryo.sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("usage_type", sa.Text(), nullable=False),
        sa.Column("bucket", sa.Text(), nullable=True),
        sa.Column("resource_id", sa.Text(), nullable=True),
        sa.Column("operation", sa.Text(), nullable=True),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column("unblended_cost", sa.Numeric(18, 8), nullable=False),
        sa.Column("usage_quantity", sa.Numeric(18, 8), nullable=True),
        sa.Column("usage_unit", sa.Text(), nullable=True),
        sa.Column(
            "ingested_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema="cryo",
    )
    op.create_index(
        "idx_cur_source_date",
        "aws_cur_line_items",
        ["source_id", "usage_date"],
        schema="cryo",
    )
    op.create_index(
        "idx_cur_bucket_date",
        "aws_cur_line_items",
        ["bucket", "usage_date"],
        schema="cryo",
    )

    op.create_table(
        "aws_inventory_objects",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "source_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cryo.sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("inventory_date", sa.Date(), nullable=False),
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_class", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("table_prefix", sa.Text(), nullable=True),
        schema="cryo",
    )
    op.create_index(
        "idx_inv_source_date",
        "aws_inventory_objects",
        ["source_id", "inventory_date"],
        schema="cryo",
    )
    op.create_index(
        "idx_inv_bucket_key",
        "aws_inventory_objects",
        ["bucket", "key"],
        schema="cryo",
    )
    op.create_index(
        "idx_inv_prefix",
        "aws_inventory_objects",
        ["source_id", "table_prefix"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index("idx_inv_prefix", table_name="aws_inventory_objects", schema="cryo")
    op.drop_index("idx_inv_bucket_key", table_name="aws_inventory_objects", schema="cryo")
    op.drop_index("idx_inv_source_date", table_name="aws_inventory_objects", schema="cryo")
    op.drop_table("aws_inventory_objects", schema="cryo")

    op.drop_index("idx_cur_bucket_date", table_name="aws_cur_line_items", schema="cryo")
    op.drop_index("idx_cur_source_date", table_name="aws_cur_line_items", schema="cryo")
    op.drop_table("aws_cur_line_items", schema="cryo")

    op.drop_table("aws_cost_insight_configs", schema="cryo")