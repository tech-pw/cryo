"""Add membership_status to incident_cohort_members.

Revision ID: 20260920_0053
Revises: 20260920_0052
Create Date: 2026-09-20 00:00:04.000000

Review finding F11, 2026-09-20: distinguishes a run that merely matched the incident window
query ("candidate") from one independently verified to actually exhibit the incident's symptom
("confirmed") or verified NOT to be affected ("excluded") -- see
cryo/db/models.py::IncidentCohortMember.membership_status's docstring.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260920_0053"
down_revision = "20260920_0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "incident_cohort_members",
        sa.Column(
            "membership_status", sa.Text(), nullable=False, server_default=sa.text("'candidate'")
        ),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("incident_cohort_members", "membership_status", schema="cryo")
