"""Unset is_default on all presets.

Removes `native iceberg-rust` as the platform default maintenance engine. No preset is
shipped with `is_default = true`; tables without an explicit assignment or matching rule
resolve to `provenance = "none"`, and the maintenance run API rejects requests without
an explicit `preset_id`. Users must select a preset per table before queueing a run.

Revision ID: 20260707_0020
Revises: 20260707_0019
Create Date: 2026-07-07 17:30:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "20260707_0020"
down_revision = "20260707_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE cryo.presets SET is_default = false WHERE is_default = true")


def downgrade() -> None:
    # Best-effort: re-mark the seeded native preset as default if it still exists.
    # Not guaranteed to be reversible for installations that deleted/renamed it.
    op.execute(
        "UPDATE cryo.presets SET is_default = true "
        "WHERE name = 'default-native-deep' AND enabled = true"
    )
