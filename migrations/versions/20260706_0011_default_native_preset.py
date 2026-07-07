"""default native deep preset bootstrap

Revision ID: 20260706_0011
Revises: 20260706_0010
Create Date: 2026-07-06 18:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260706_0011"
down_revision = "20260706_0010"
branch_labels = None
depends_on = None

DEFAULT_DESTINATION_NAME = "native-datafusion"
DEFAULT_PRESET_NAME = "default-native-deep"


def upgrade() -> None:
    op.add_column(
        "presets",
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema="cryo",
    )

    op.execute(
        """
        CREATE UNIQUE INDEX uq_presets_one_default_per_tenant
        ON cryo.presets (COALESCE(tenant_id, '00000000-0000-0000-0000-000000000000'::uuid))
        WHERE is_default = true AND enabled = true
        """
    )

    # Idempotent bootstrap for fresh installs.
    op.execute(
        f"""
        INSERT INTO cryo.destinations (name, engine, config, enabled)
        SELECT
            '{DEFAULT_DESTINATION_NAME}',
            'native',
            '{{"slot_cap": 5, "operation_mode": "execute", "timeout_seconds": 3600}}'::jsonb,
            true
        WHERE NOT EXISTS (
            SELECT 1 FROM cryo.destinations WHERE name = '{DEFAULT_DESTINATION_NAME}'
        )
        """
    )

    op.execute(
        f"""
        INSERT INTO cryo.presets (
            name,
            description,
            destination_id,
            mode,
            operations,
            params,
            schedule_cron,
            priority,
            enabled,
            guardrails,
            is_default
        )
        SELECT
            '{DEFAULT_PRESET_NAME}',
            'Default deep maintenance via iceberg-rust/DataFusion (manual runs only)',
            d.id,
            'deep',
            '["expire_snapshots"]'::jsonb,
            '{{"min_small_files": 10, "retention_days": 7, "retain_last": 1, "orphan_retention_days": 7}}'::jsonb,
            NULL,
            0,
            true,
            '{{"cooldown_hours": 24}}'::jsonb,
            true
        FROM cryo.destinations d
        WHERE d.name = '{DEFAULT_DESTINATION_NAME}'
          AND NOT EXISTS (
              SELECT 1 FROM cryo.presets WHERE name = '{DEFAULT_PRESET_NAME}'
          )
        """
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM cryo.presets WHERE name = '{DEFAULT_PRESET_NAME}'")
    op.execute(f"DELETE FROM cryo.destinations WHERE name = '{DEFAULT_DESTINATION_NAME}'")
    op.execute("DROP INDEX IF EXISTS cryo.uq_presets_one_default_per_tenant")
    op.drop_column("presets", "is_default", schema="cryo")
