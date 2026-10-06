"""retire the duplicate spark-k8s destination when another Spark destination exists

Revision ID: 20260930_1600
Revises: 20260929_1100
Create Date: 2026-09-30 16:00:00.000000

20260929_1100 seeded a `spark-k8s` destination for the fleet-wide default preset without
checking for an existing Spark destination. One install already had another Spark destination (the one
carrying the real catalog URI and pod sizing), and the batch coordinator requires exactly one
enabled spark_emr destination -- so every nightly batch since has failed with "expected exactly
one enabled Spark destination, found 2". Repoint the default preset at the pre-existing Spark
destination and disable the seeded one. Installs where `spark-k8s` is the only Spark
destination are left untouched.
"""

from __future__ import annotations

from alembic import op

revision = "20260930_1600"
down_revision = "20260929_1100"
branch_labels = None
depends_on = None

SEEDED_DESTINATION_NAME = "spark-k8s"
DEFAULT_PRESET_NAME = "default-spark-compact"


def upgrade() -> None:
    op.execute(
        f"""
        WITH other AS (
            SELECT id FROM cryo.destinations
            WHERE engine = 'spark_emr' AND enabled = true
              AND name <> '{SEEDED_DESTINATION_NAME}'
            ORDER BY created_at
            LIMIT 1
        )
        UPDATE cryo.presets p
        SET destination_id = other.id
        FROM other
        WHERE p.name = '{DEFAULT_PRESET_NAME}'
        """
    )
    op.execute(
        f"""
        UPDATE cryo.destinations d
        SET enabled = false
        WHERE d.name = '{SEEDED_DESTINATION_NAME}'
          AND EXISTS (
              SELECT 1 FROM cryo.destinations o
              WHERE o.engine = 'spark_emr' AND o.enabled = true
                AND o.name <> '{SEEDED_DESTINATION_NAME}'
          )
          AND NOT EXISTS (
              SELECT 1 FROM cryo.presets p WHERE p.destination_id = d.id
          )
        """
    )


def downgrade() -> None:
    op.execute(
        f"UPDATE cryo.destinations SET enabled = true WHERE name = '{SEEDED_DESTINATION_NAME}'"
    )
    op.execute(
        f"""
        UPDATE cryo.presets SET destination_id = (
            SELECT id FROM cryo.destinations WHERE name = '{SEEDED_DESTINATION_NAME}'
        )
        WHERE name = '{DEFAULT_PRESET_NAME}'
        """
    )
