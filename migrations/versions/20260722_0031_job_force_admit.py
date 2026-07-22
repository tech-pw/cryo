"""Add force_admit flag to jobs so a manually-triggered run can bypass soft
scheduling gates (cooldown, benefit/moop score, offpeak window) that would
otherwise leave it stuck cycling through queued/deferred with no way for an
operator to unstick it short of editing Redis/DB state directly.

Revision ID: 20260722_0031
Revises: 20260720_0030
Create Date: 2026-07-22 12:00:00.000000

Table locks, PII gates, and ABAC checks are unaffected -- those are real
safety gates, not scheduling policy, and force_admit does not bypass them.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260722_0031"
down_revision = "20260720_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "force_admit",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("jobs", "force_admit", schema="cryo")
