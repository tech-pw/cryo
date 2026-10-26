"""table_alert_subscriptions.created_by

Revision ID: 20261026_1200
Revises: 20261022_1200

Records who created a table alert subscription so that only the subscriber (or an admin)
can remove it. Existing rows keep NULL; for those, the subscribed email standing in for
the caller's identity, or an admin, still allows deletion.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261026_1200"
down_revision = "20261022_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "table_alert_subscriptions",
        sa.Column("created_by", sa.Text(), nullable=True),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("table_alert_subscriptions", "created_by", schema="cryo")
