"""Set default-native-deep chunk_size_gb=1 and retain_last=4.

Revision ID: 20260709_0026
Revises: 20260708_0025
Create Date: 2026-07-09 11:15:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "20260709_0026"
down_revision = "20260708_0025"
branch_labels = None
depends_on = None

DEFAULT_PRESET_NAME = "default-native-deep"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE cryo.presets
        SET params = params
            || '{{"chunk_size_gb": 1, "retain_last": 4}}'::jsonb
        WHERE name = '{DEFAULT_PRESET_NAME}'
        """
    )


def downgrade() -> None:
    # Restore pre-change retain_last; drop chunk_size_gb (was unset on the seeded preset).
    op.execute(
        f"""
        UPDATE cryo.presets
        SET params = (params - 'chunk_size_gb')
            || '{{"retain_last": 1}}'::jsonb
        WHERE name = '{DEFAULT_PRESET_NAME}'
        """
    )
