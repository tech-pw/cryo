"""per-destination query-log ingestion config + retention

Revision ID: 20260707_0018
Revises: 20260707_0017
Create Date: 2026-07-07 00:00:00.000000

Adds three columns on ``cryo.destinations`` that drive the new tiered query-log pipeline:

- ``query_log_config``  -- JSONB block carrying per-destination ingestion mode
  (``trino_system_runtime`` for the poll-Trino path, ``read_through`` for pointing at
  an existing Iceberg-native query log the user already maintains). Keeps Postgres
  metadata DB from being written into 1M-queries/day hot path; the curator drains a
  Redis Stream and ships everything else into Iceberg.
- ``query_log_retention_days`` -- days to keep raw rows in the Cryo Iceberg archive
  via ``expire_snapshots``. NULL = no auto-expiry (used for ``read_through`` mode,
  where the source-of-truth table lives outside Cryo).
- ``query_log_watermark`` -- last successfully-archived ``executed_at``, so the
  curator recovers from crashes without duplicating rows into Iceberg.

The Iceberg table itself (``cryo.queries``) is created lazily on first write by
``cryo/telemetry/iceberg_writer.py``; no schema lives in this migration because the
catalog is the Iceberg catalog, not Postgres.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260707_0018"
down_revision = "20260707_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "destinations",
        sa.Column("query_log_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "destinations",
        sa.Column(
            "query_log_retention_days",
            sa.Integer(),
            nullable=True,
            comment="NULL means no auto-expiry (read-through mode); default 30 when first enabled.",
        ),
        schema="cryo",
    )
    op.add_column(
        "destinations",
        sa.Column(
            "query_log_watermark",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment="High-water mark of executed_at successfully archived to Iceberg.",
        ),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("destinations", "query_log_watermark", schema="cryo")
    op.drop_column("destinations", "query_log_retention_days", schema="cryo")
    op.drop_column("destinations", "query_log_config", schema="cryo")