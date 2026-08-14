"""Add query_cost_attribution_daily for query-driven cost attribution/chargeback.

Revision ID: 20260814_0042
Revises: 20260814_0041
Create Date: 2026-08-14 16:00:00.000000

Trino's own query-completion event (already read by cryo.telemetry.iceberg_source for
selectivity, see the 2026-08-14 avg_selectivity fix) carries per-query, per-input-table
bytes-scanned data in inputs_json, plus a "source" field identifying the client/BI tool that
ran the query (Metabase, dbt, DBeaver, trino-python-client, etc). Mining a 20K-query sample
found Metabase alone responsible for 1.5TB scanned, with individual queries scanning hundreds
of GB for near-zero output (up to a 58-billion-to-1 input:output ratio on one DBeaver query) --
real, directly attributable cost with no extra instrumentation needed.

query_cost_attribution_daily rolls this up to (day, client, schema_name) -- coarse enough to
stay small and directly useful for a "who/what is driving scan cost" report, without needing a
per-query audit trail (Trino's own trino_queries table already IS that audit trail, and this
system reads it read-through rather than duplicating it -- see iceberg_source.py's module
docstring). Same shape discipline as S3RequestCostDaily: one bounded, retained-window table,
pruned on a schedule (see cryo.telemetry.cost_attribution.prune_query_cost_attribution).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260814_0042"
down_revision = "20260814_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "query_cost_attribution_daily",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cryo.sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day", sa.Date(), nullable=False),
        # Normalized client identity -- e.g. "Metabase v0.60.0", not the raw source string
        # which includes a per-dashboard UUID (see cost_attribution.py's _normalize_client).
        sa.Column("client", sa.Text(), nullable=False),
        sa.Column("schema_name", sa.Text(), nullable=False),
        sa.Column("query_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("failed_query_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("physical_bytes_scanned", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("estimated_scan_cost_usd", sa.Float(), nullable=True),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "source_id", "day", "client", "schema_name", name="uq_query_cost_attribution_key"
        ),
        schema="cryo",
    )
    op.create_index(
        "ix_query_cost_attribution_day",
        "query_cost_attribution_daily",
        ["day"],
        schema="cryo",
    )
    op.create_index(
        "ix_query_cost_attribution_client",
        "query_cost_attribution_daily",
        ["client"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_query_cost_attribution_client",
        table_name="query_cost_attribution_daily",
        schema="cryo",
    )
    op.drop_index(
        "ix_query_cost_attribution_day", table_name="query_cost_attribution_daily", schema="cryo"
    )
    op.drop_table("query_cost_attribution_daily", schema="cryo")
