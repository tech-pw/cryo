"""Backfill engine_impl=go on native destinations and update preset description."""

from __future__ import annotations

from alembic import op

revision = "20260708_0022"
down_revision = "20260707_0021"
branch_labels = None
depends_on = None

DEFAULT_PRESET_NAME = "default-native-deep"


def upgrade() -> None:
    op.execute(
        """
        UPDATE cryo.destinations
        SET config = config || '{"engine_impl": "go"}'::jsonb
        WHERE engine = 'native'
          AND (config->>'engine_impl') IS NULL
        """
    )
    op.execute(
        f"""
        UPDATE cryo.presets
        SET description = (
            'Native deep maintenance: expire + orphan + COW optimize (iceberg-go); '
            'MOR optimize and manifests route to sql_fallback_destination_id (Trino).'
        )
        WHERE name = '{DEFAULT_PRESET_NAME}'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE cryo.destinations
        SET config = config - 'engine_impl'
        WHERE engine = 'native'
          AND config->>'engine_impl' = 'go'
          AND config ? 'engine_impl'
        """
    )
