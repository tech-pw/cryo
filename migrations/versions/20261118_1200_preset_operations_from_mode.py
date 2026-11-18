"""Rewrite each preset's stored operations to the list its mode runs (#594)

Revision ID: 20261118_1200
Revises: 20261116_1000

Scheduled runs take their operations from the preset's mode (MODE_OPERATIONS), not from
``cryo.presets.operations``. Rows written before #567 still hold whatever the old checkboxes
saved, so a deep preset could read ``["optimize"]`` while its runs also rewrote manifests,
expired snapshots and removed orphans. This sets every row with a known mode to that mode's
list in CORRECT_ORDER. A row whose mode is not one of the four below is left alone.

MODE_OPERATION_LISTS is a snapshot of ``GET /presets/modes`` (cryo/planner/guardrails.py) on
2026-11-18, kept literal so later changes to the mode table cannot rewrite history here;
tests/test_preset_operations_from_mode_migration.py pins it to the code as of this revision.
"""

from __future__ import annotations

import json

from alembic import op

revision = "20261118_1200"
down_revision = "20261116_1000"
branch_labels = None
depends_on = None

MODE_OPERATION_LISTS: dict[str, tuple[str, ...]] = {
    "light": ("expire_data", "expire_snapshots"),
    "standard": (
        "convert_equality_deletes",
        "convert_deletes",
        "rewrite_position_deletes",
        "optimize",
        "compute_statistics",
        "expire_data",
        "expire_snapshots",
    ),
    "deep": (
        "convert_equality_deletes",
        "convert_deletes",
        "rewrite_position_deletes",
        "optimize",
        "compute_statistics",
        "optimize_manifests",
        "expire_data",
        "expire_snapshots",
        "remove_orphan_files",
    ),
    "manifest_only": ("optimize_manifests",),
}


def rewrite_sql() -> str:
    cases = "\n            ".join(
        f"WHEN '{mode}' THEN '{json.dumps(list(ops))}'::jsonb"
        for mode, ops in MODE_OPERATION_LISTS.items()
    )
    modes = ", ".join(f"'{mode}'" for mode in MODE_OPERATION_LISTS)
    target = f"CASE mode\n            {cases}\n        END"
    return f"""
        UPDATE cryo.presets
        SET operations = {target}
        WHERE mode IN ({modes})
          AND operations IS DISTINCT FROM {target}
    """


def upgrade() -> None:
    op.execute(rewrite_sql())


def downgrade() -> None:
    # The pre-upgrade lists are not kept anywhere, and nothing schedules from them, so there
    # is nothing to restore.
    pass
