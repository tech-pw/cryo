"""Phase 2 migration: pair default-native-deep expire with remove_orphan_files."""

from __future__ import annotations

from alembic import op

revision = "20260707_0013"
down_revision = "20260707_0012"
branch_labels = None
depends_on = None

DEFAULT_PRESET_NAME = "default-native-deep"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE cryo.presets
        SET
            operations = '["expire_snapshots", "remove_orphan_files"]'::jsonb,
            description = 'Default native maintenance (expire_snapshots + remove_orphan_files; optimize deferred)'
        WHERE name = '{DEFAULT_PRESET_NAME}'
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE cryo.presets
        SET
            operations = '["expire_snapshots"]'::jsonb,
            description = 'Default native maintenance (expire_snapshots only until orphan removal ships)'
        WHERE name = '{DEFAULT_PRESET_NAME}'
        """
    )
