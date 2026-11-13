"""Seeded default preset runs deep mode so scheduled maintenance removes orphan files

Revision ID: 20261113_1300
Revises: 20261113_1200

The bootstrap default (20260929_1100) is mode='standard', and standard never plans
remove_orphan_files or optimize_manifests, so a fleet on the default never cleaned orphans.
Only the row that still holds every seeded value is switched. A default an operator edited
(any of mode, operations, params, guardrails or description changed) is left alone; the
release notes tell them how to opt in. The per-table orphan cadence keeps daily deep runs
cheap (cryo/planner/orphan_cadence.py).
"""

from __future__ import annotations

from alembic import op

revision = "20261113_1300"
down_revision = "20261113_1200"
branch_labels = None
depends_on = None

PRESET_NAME = "default-spark-compact"
SEEDED_DESCRIPTION = (
    "Fleet-wide default: compact on Spark for any table with no explicit assignment or "
    "matching rule"
)
DEEP_DESCRIPTION = (
    "Fleet-wide default: compact, expire snapshots, rewrite manifests and remove orphan files "
    "(per-table cadence) on Spark for any table with no explicit assignment or matching rule"
)
SEEDED_OPERATIONS = '["optimize"]'
DEEP_OPERATIONS = '["optimize", "optimize_manifests", "expire_snapshots", "remove_orphan_files"]'
SEEDED_PARAMS = '{"min_small_files": 10}'
SEEDED_GUARDRAILS = '{"cooldown_hours": 24}'


def _flip(
    *,
    from_mode: str,
    to_mode: str,
    from_ops: str,
    to_ops: str,
    from_description: str,
    to_description: str,
    name: str = PRESET_NAME,
) -> str:
    # The cron is not compared: 20261026_1710 filled a missing one with the default cron.
    return f"""
        UPDATE cryo.presets
        SET mode = '{to_mode}',
            operations = '{to_ops}'::jsonb,
            description = '{to_description}'
        WHERE name = '{name}'
          AND is_default = true
          AND mode = '{from_mode}'
          AND operations = '{from_ops}'::jsonb
          AND params = '{SEEDED_PARAMS}'::jsonb
          AND guardrails = '{SEEDED_GUARDRAILS}'::jsonb
          AND description = '{from_description}'
        """


def upgrade() -> None:
    op.execute(
        _flip(
            from_mode="standard",
            to_mode="deep",
            from_ops=SEEDED_OPERATIONS,
            to_ops=DEEP_OPERATIONS,
            from_description=SEEDED_DESCRIPTION,
            to_description=DEEP_DESCRIPTION,
        )
    )


def downgrade() -> None:
    op.execute(
        _flip(
            from_mode="deep",
            to_mode="standard",
            from_ops=DEEP_OPERATIONS,
            to_ops=SEEDED_OPERATIONS,
            from_description=DEEP_DESCRIPTION,
            to_description=SEEDED_DESCRIPTION,
        )
    )
