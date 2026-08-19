"""Add user_name to query_cost_attribution_daily for user-level cost attribution.

Revision ID: 20260819_0045
Revises: 20260818_0044
Create Date: 2026-08-19 12:00:00.000000

Follow-up to 20260814_0042: the query-cost report currently ranks scan cost by *client*
(Metabase, dbt-trino, ...), not by the individual Trino user who ran the query. See
docs/QUERY_COST_USER_ATTRIBUTION_PLAN_2026-08.md for the full rationale -- summary: Trino's
own query-completion event already carries a "user" field (this codebase's established name
for it is "user_name" -- see cryo.telemetry.iceberg_writer._build_query_log_schema and
cryo.telemetry.ingestion.py's `user AS user_name`), the read-through cost-attribution path
in cryo.telemetry.cost_attribution.py just never read it.

Existing rows have no way to know their true user split retroactively -- the (day, client,
schema_name) rollup already collapsed that dimension before this migration existed. Every
row created before this migration lands under user_name='unknown' after backfill, same
fallback string _normalize_client already uses for an unidentified client. Only NEW rows,
ingested after cryo.telemetry.cost_attribution.py's read path is updated to read the
configured user column (separate follow-up commit, not this migration), will have a real
user_name. This is expected, not a bug -- flagging explicitly per the plan doc's own
"backfill consideration" so nobody mistakes an all-'unknown' historical row for broken
ingestion.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260819_0045"
down_revision = "20260818_0044"
branch_labels = None
depends_on = None

_OLD_CONSTRAINT = "uq_query_cost_attribution_key"


def upgrade() -> None:
    op.add_column(
        "query_cost_attribution_daily",
        sa.Column("user_name", sa.Text(), nullable=False, server_default="unknown"),
        schema="cryo",
    )
    op.drop_constraint(
        _OLD_CONSTRAINT, "query_cost_attribution_daily", schema="cryo", type_="unique"
    )
    op.create_unique_constraint(
        _OLD_CONSTRAINT,
        "query_cost_attribution_daily",
        ["source_id", "day", "client", "schema_name", "user_name"],
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_constraint(
        _OLD_CONSTRAINT, "query_cost_attribution_daily", schema="cryo", type_="unique"
    )
    op.create_unique_constraint(
        _OLD_CONSTRAINT,
        "query_cost_attribution_daily",
        ["source_id", "day", "client", "schema_name"],
        schema="cryo",
    )
    op.drop_column("query_cost_attribution_daily", "user_name", schema="cryo")
