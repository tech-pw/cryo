"""Add head_requests_30d and delete_requests_30d to table_cost.

Revision ID: 20260908_0047
Revises: 20260819_0046

docs/s3-total-cost-optimization.md §1.1 (P0#2/#3): HEAD requests were previously silently
folded into get_requests_30d by recompute_table_cost_rollup, so a real HEAD regression
(confirmed live: 57.7% of tables got MORE HEAD requests after maintenance, statistically
significant the wrong direction) had no dedicated field for the UI to ever surface -- it just
inflated the GET number. delete_requests_30d is tracked for operational-load visibility
(throttling risk, cleanup volume) even though DELETE requests themselves are free -- see the
same doc's P0#1 and cryo.scanner.s3_request_cost_ingest._operation_tier's docstring for the
paired pricing-classification fix (DELETE was previously mispriced at the Tier-1 LIST rate).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0047"
down_revision = "20260819_0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "table_cost",
        sa.Column("head_requests_30d", sa.BigInteger(), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "table_cost",
        sa.Column("delete_requests_30d", sa.BigInteger(), nullable=True),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("table_cost", "delete_requests_30d", schema="cryo")
    op.drop_column("table_cost", "head_requests_30d", schema="cryo")
