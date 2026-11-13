"""S3 bucket versioning/lifecycle status

Revision ID: 20261113_1200
Revises: 20261112_1200

With versioning on and no noncurrent-version expiration rule, deleting files does not lower
the S3 bill. The scanner records each bucket's state so the cost API can warn (#538).
Additive: a new table only.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261113_1200"
down_revision = "20261112_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "s3_bucket_status",
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("versioning", sa.Text(), nullable=True),
        sa.Column("noncurrent_expiration", sa.Boolean(), nullable=True),
        sa.Column("checked_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("bucket", name="pk_s3_bucket_status"),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_table("s3_bucket_status", schema="cryo")
