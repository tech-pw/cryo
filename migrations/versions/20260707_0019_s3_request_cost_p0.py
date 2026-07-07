"""S3 request-cost tracking (P0)

Revision ID: 20260707_0019_s3_request_cost_p0
Revises: 20260707_0020
Create Date: 2026-07-07 00:00:00.000000

Per ``docs/s3-request-cost-tracking.md`` §12 P0:

- Add ``tables.location`` (nullable VARCHAR) so the §4.1 bucket discovery
  (``SELECT DISTINCT bucket FROM tables WHERE source_id = X``) works without a
  separate catalog-discovery step.
- Add ``s3_bucket_log_configs`` (per-(source, bucket) S3 server access-log ingestion
  config, including its own ``access_key_id``/``secret_ref`` credential pair -- see
  §6.2, "deliberately explicit, never implicitly borrowed").
- Add ``s3_request_cost_daily`` (per-bucket, per-day, per-table, per-operation-tier
  UPSERT-able rows, unique on the full grouping key so ``ON CONFLICT`` is actually
  usable) as the bounded, retained-window audit trail for §5 step 5.
- Add the rolling-30-day summary columns to ``table_cost`` (§6.3) -- this is the
  one-row-per-table read path the UI queries; ``s3_request_cost_daily`` above is the
  ingestion write/audit path a retention job prunes behind it.

This migration is additive only -- one new column on ``tables``, five new nullable
columns on ``table_cost``, and two new tables. No existing data is altered;
ingestion only starts when an operator enables a bucket via the §8 endpoints (P1+).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260707_0019_s3_request_cost_p0"
down_revision = "20260707_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tables",
        sa.Column("location", sa.Text(), nullable=True),
        schema="cryo",
    )

    op.add_column(
        "table_cost",
        sa.Column("get_requests_30d", sa.BigInteger(), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "table_cost",
        sa.Column("put_requests_30d", sa.BigInteger(), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "table_cost",
        sa.Column("list_requests_30d", sa.BigInteger(), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "table_cost",
        sa.Column("request_cost_usd_month", sa.Float(), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "table_cost",
        sa.Column("request_cost_pricing_source", sa.Text(), nullable=True),
        schema="cryo",
    )

    op.create_table(
        "s3_bucket_log_configs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cryo.sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column("access_key_id", sa.Text(), nullable=True),
        sa.Column("secret_ref", sa.Text(), nullable=True),
        sa.Column("logging_status", sa.Text(), nullable=False, server_default=sa.text("'unknown'")),
        sa.Column("log_target_bucket", sa.Text(), nullable=True),
        sa.Column("log_target_prefix", sa.Text(), nullable=True),
        sa.Column("manual_override", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("cryo_added_policy_sids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_checked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_ingested_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_ingested_date", sa.Date(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_error_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("source_id", "bucket", name="uq_s3_log_config_source_bucket"),
        schema="cryo",
    )
    op.create_index(
        "ix_s3_log_config_source",
        "s3_bucket_log_configs",
        ["source_id"],
        schema="cryo",
    )

    op.create_table(
        "s3_request_cost_daily",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cryo.sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column(
            "table_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cryo.tables.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("operation_tier", sa.Text(), nullable=False),
        sa.Column("request_count", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "pricing_source",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'fallback_default'"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # NOTE: Postgres treats every NULL as distinct for uniqueness, so this does not dedupe
        # table_id IS NULL rows (log entries matching no known table prefix). See the model
        # docstring in cryo/db/models.py for how the P1 ingestion writer must handle that case.
        sa.UniqueConstraint(
            "source_id",
            "bucket",
            "table_id",
            "day",
            "operation_tier",
            name="uq_s3_request_cost_daily_row",
        ),
        schema="cryo",
    )
    op.create_index(
        "ix_s3_request_cost_daily_table_day",
        "s3_request_cost_daily",
        ["table_id", "day"],
        schema="cryo",
    )
    op.create_index(
        "ix_s3_request_cost_daily_source_bucket_day",
        "s3_request_cost_daily",
        ["source_id", "bucket", "day"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_s3_request_cost_daily_source_bucket_day",
        table_name="s3_request_cost_daily",
        schema="cryo",
    )
    op.drop_index(
        "ix_s3_request_cost_daily_table_day",
        table_name="s3_request_cost_daily",
        schema="cryo",
    )
    op.drop_table("s3_request_cost_daily", schema="cryo")
    op.drop_index(
        "ix_s3_log_config_source",
        table_name="s3_bucket_log_configs",
        schema="cryo",
    )
    op.drop_table("s3_bucket_log_configs", schema="cryo")

    op.drop_column("table_cost", "request_cost_pricing_source", schema="cryo")
    op.drop_column("table_cost", "request_cost_usd_month", schema="cryo")
    op.drop_column("table_cost", "list_requests_30d", schema="cryo")
    op.drop_column("table_cost", "put_requests_30d", schema="cryo")
    op.drop_column("table_cost", "get_requests_30d", schema="cryo")

    op.drop_column("tables", "location", schema="cryo")
