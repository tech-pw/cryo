"""Add task_state to analysis_tasks.

Revision ID: 20260920_0052
Revises: 20260920_0051
Create Date: 2026-09-20 00:00:03.000000

Review finding F08, 2026-09-20: opaque, task-type-defined extra checkpoint state that doesn't
fit the single-string cursor field -- see cryo/db/models.py::AnalysisTask.task_state's docstring
for the specific bug this exists to fix (realized-value refresh budget resetting per-batch
instead of being consumed once per fleet generation).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260920_0052"
down_revision = "20260920_0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analysis_tasks",
        sa.Column("task_state", postgresql.JSONB(), nullable=True),
        schema="cryo",
    )


def downgrade() -> None:
    op.drop_column("analysis_tasks", "task_state", schema="cryo")
