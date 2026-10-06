"""Phase 0 migration: gate default-native-deep to expire_snapshots only."""

from __future__ import annotations

from alembic import op

revision = "20260707_0012"
down_revision = "20260706_0011"
branch_labels = None
depends_on = None

DEFAULT_PRESET_NAME = "default-native-deep"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE cryo.presets
        SET
            operations = '["expire_snapshots"]'::jsonb,
            params = params || '{{"orphan_retention_days": 7}}'::jsonb,
            description = 'Default native maintenance (expire_snapshots only until orphan removal ships)'
        WHERE name = '{DEFAULT_PRESET_NAME}'
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE cryo.presets
        SET
            operations = '["expire_snapshots", "remove_orphan_files", "optimize", "optimize_manifests"]'::jsonb,
            description = 'Default deep maintenance via iceberg-rust/DataFusion (manual runs only)'
        WHERE name = '{DEFAULT_PRESET_NAME}'
        """
    )
