"""Strip chunk_size_gb from preset params (derived from pod mem at runtime).

Revision ID: 20260720_0030
Revises: 20260720_0029
Create Date: 2026-07-20 18:55:00.000000

Native maintenance derives planner chunk_size_gb from the K8s Job memory
request. Preset-owned chunk_size_gb is removed so operators cannot pin pods
to undersized rewrite budgets.

Renumbered from 20260720_0028 (see 20260720_0029_native_dispatches.py).
"""

from __future__ import annotations

from alembic import op

revision = "20260720_0030"
down_revision = "20260720_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE cryo.presets AS preset
        SET params = preset.params - 'chunk_size_gb'
        FROM cryo.destinations AS destination
        WHERE preset.destination_id = destination.id
          AND destination.engine = 'native'
          AND preset.params ? 'chunk_size_gb'
        """
    )


def downgrade() -> None:
    # Cannot restore prior per-preset values; native deep historically used 1.
    op.execute(
        """
        UPDATE cryo.presets AS preset
        SET params = preset.params || '{"chunk_size_gb": 100}'::jsonb
        FROM cryo.destinations AS destination
        WHERE preset.destination_id = destination.id
          AND destination.engine = 'native'
          AND NOT (preset.params ? 'chunk_size_gb')
        """
    )
