"""Partial indexes for the scheduler's last-cron-run lookups on cryo.runs

Revision ID: 20261026_1330
Revises: 20261026_1720

#245 made the scheduler read the newest cron run per preset, and the newest cron scan, from
Postgres on every tick, so a Redis restart can't re-fire the day's crons. cryo.runs had no
index on preset_id, trigger or kind, so both reads scanned the whole table each tick. Cron
runs are a small share of the table, so partial indexes on trigger = 'cron' stay small.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261026_1330"
down_revision = "20261026_1720"
branch_labels = None
depends_on = None

_CRON = sa.text("trigger = 'cron'")
_INDEXES = (
    ("ix_runs_cron_preset", ["preset_id"]),
    ("ix_runs_cron_kind_created", ["kind", "created_at"]),
)


def upgrade() -> None:
    # CONCURRENTLY: runs is written by the scheduler, API and finalizer; a plain CREATE INDEX
    # blocks those writes. A failed earlier build leaves an INVALID index that IF NOT EXISTS
    # would keep, so drop it first.
    with op.get_context().autocommit_block():
        for name, columns in _INDEXES:
            op.drop_index(
                name, table_name="runs", schema="cryo", postgresql_concurrently=True, if_exists=True
            )
            op.create_index(
                name,
                "runs",
                columns,
                schema="cryo",
                postgresql_where=_CRON,
                postgresql_concurrently=True,
                if_not_exists=True,
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, _columns in _INDEXES:
            op.drop_index(
                name, table_name="runs", schema="cryo", postgresql_concurrently=True, if_exists=True
            )
