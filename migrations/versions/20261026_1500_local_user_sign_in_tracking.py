"""track how local users were created and when they last signed in

Revision ID: 20261026_1500
Revises: 20261022_1200
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261026_1500"
down_revision = "20261022_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("local_users", sa.Column("created_via", sa.Text(), nullable=True), schema="cryo")
    op.add_column(
        "local_users",
        sa.Column("last_login_at", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="cryo",
    )
    op.add_column(
        "local_users", sa.Column("last_login_method", sa.Text(), nullable=True), schema="cryo"
    )


def downgrade() -> None:
    op.drop_column("local_users", "last_login_method", schema="cryo")
    op.drop_column("local_users", "last_login_at", schema="cryo")
    op.drop_column("local_users", "created_via", schema="cryo")
