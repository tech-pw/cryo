"""Add applied_prune_efficiency_baseline to layout_recommendations.

Revision ID: 20260817_0043
Revises: 20260814_0042
Create Date: 2026-08-17 22:00:00.000000

PDAE-8288: LayoutRecommendation.observed_prune_improvement exists and is read into every
API response, but nothing ever writes it -- there is no scheduled measurement of whether an
applied sort/z-order rewrite actually improved pruning. The existing current_prune_efficiency
column can't serve as the "before" baseline for that comparison: generate_layout_recommendation
overwrites it in place on every regeneration (no status check on the update path), so a
recommendation's pre-apply baseline silently gets clobbered by the next routine refresh cycle
once 24h pass and the table's query_patterns update -- confirmed live on
lsq_pw.silver_prospect_activity_base, applied 2026-08-11, 6 days ago at time of writing.

applied_prune_efficiency_baseline is a permanent, write-once snapshot captured at
execute_layout_recommendation time (the moment a rewrite is actually dispatched), immune to
any later regeneration tick touching current_prune_efficiency.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260817_0043"
down_revision = "20260814_0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "layout_recommendations",
        sa.Column("applied_prune_efficiency_baseline", sa.Float(), nullable=True),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("layout_recommendations", "applied_prune_efficiency_baseline", schema="cryo")
