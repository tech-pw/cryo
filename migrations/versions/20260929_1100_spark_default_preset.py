"""default spark-on-k8s preset bootstrap

Revision ID: 20260929_1100
Revises: 20260929_1000
Create Date: 2026-09-29 21:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "20260929_1100"
down_revision = "20260929_1000"
branch_labels = None
depends_on = None

DEFAULT_DESTINATION_NAME = "spark-k8s"
DEFAULT_PRESET_NAME = "default-spark-compact"
PRIOR_DEFAULT_PRESET_NAME = "default-native-deep"


def upgrade() -> None:
    # Idempotent bootstrap for fresh installs. submit_mode="k8s" routes through the
    # already-deployed Spark-on-Kubernetes dispatch (cryo/engines/spark/k8s.py,
    # deploy/helm/cryo/values-prod.yaml's spark.k8s.* block) -- no EMR Serverless
    # application_id/execution_role_arn exists for this install, and none is required
    # for this submit mode.
    op.execute(
        f"""
        INSERT INTO cryo.destinations (name, engine, config, enabled)
        SELECT
            '{DEFAULT_DESTINATION_NAME}',
            'spark_emr',
            '{{"submit_mode": "k8s"}}'::jsonb,
            true
        WHERE NOT EXISTS (
            SELECT 1 FROM cryo.destinations WHERE name = '{DEFAULT_DESTINATION_NAME}'
        )
        """
    )

    # uq_presets_one_default_per_tenant (20260706_0011) allows at most one is_default=true
    # preset per tenant scope. The prior default (default-native-deep) targeted the native
    # engine; product decision 2026-09-29 is Spark becomes the fleet-wide default instead,
    # so clear the old default rather than trying to hold two defaults at once.
    op.execute(
        f"""
        UPDATE cryo.presets SET is_default = false
        WHERE name = '{PRIOR_DEFAULT_PRESET_NAME}' AND is_default = true
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
            'Fleet-wide default: compact on Spark for any table with no explicit assignment or matching rule',
            d.id,
            'standard',
            '["optimize"]'::jsonb,
            '{{"min_small_files": 10}}'::jsonb,
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
    op.execute(
        f"""
        UPDATE cryo.presets SET is_default = true
        WHERE name = '{PRIOR_DEFAULT_PRESET_NAME}'
        """
    )
