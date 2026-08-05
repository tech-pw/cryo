"""Add durable maintenance intents, fencing, attempts, effects, and control state.

Revision ID: 20260804_0036
Revises: 20260731_0035
Create Date: 2026-08-04 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260804_0036"
down_revision = "20260731_0035"
branch_labels = None
depends_on = None


def _timestamps() -> tuple[sa.Column[sa.DateTime], sa.Column[sa.DateTime]]:
    return (
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def upgrade() -> None:
    op.create_table(
        "maintenance_intents",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("cryo.tenants.id")),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cryo.jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_intent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cryo.maintenance_intents.id", ondelete="SET NULL"),
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column(
            "source_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cryo.sources.id"),
            nullable=False,
        ),
        sa.Column(
            "table_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cryo.tables.id"),
            nullable=False,
        ),
        sa.Column("table_ref", sa.Text(), nullable=False),
        sa.Column(
            "destination_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cryo.destinations.id"),
            nullable=False,
        ),
        sa.Column("operation_scope", JSONB(), nullable=False),
        sa.Column("observed_snapshot_id", sa.BigInteger()),
        sa.Column("policy_id", sa.Text()),
        sa.Column("policy_version", sa.Text()),
        sa.Column("canonical_plan", JSONB(), nullable=False),
        sa.Column("plan_hash", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column(
            "approval_bindings",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("fencing_epoch", sa.BigInteger(), nullable=False),
        sa.Column("budget", JSONB(), nullable=False),
        sa.Column("capability_requirements", JSONB(), nullable=False),
        sa.Column("containment_action", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("job_id", "version", name="uq_maintenance_intents_job_version"),
        sa.UniqueConstraint("idempotency_key", name="uq_maintenance_intents_idempotency_key"),
        schema="cryo",
    )
    op.create_index(
        "ix_maintenance_intents_tenant_id",
        "maintenance_intents",
        ["tenant_id"],
        schema="cryo",
    )
    op.create_index(
        "ix_maintenance_intents_job_id", "maintenance_intents", ["job_id"], schema="cryo"
    )
    op.create_index(
        "ix_maintenance_intents_table_status",
        "maintenance_intents",
        ["table_id", "status"],
        schema="cryo",
    )
    op.create_index(
        "ix_maintenance_intents_status_expires",
        "maintenance_intents",
        ["status", "expires_at"],
        schema="cryo",
    )

    op.add_column(
        "jobs",
        sa.Column("current_intent_id", UUID(as_uuid=True)),
        schema="cryo",
    )
    op.add_column("jobs", sa.Column("intent_version", sa.Integer()), schema="cryo")
    op.create_foreign_key(
        "fk_jobs_current_intent",
        "jobs",
        "maintenance_intents",
        ["current_intent_id"],
        ["id"],
        source_schema="cryo",
        referent_schema="cryo",
        use_alter=True,
    )
    op.create_index(
        "ix_jobs_current_intent_id", "jobs", ["current_intent_id"], schema="cryo"
    )

    op.create_table(
        "maintenance_fences",
        sa.Column(
            "table_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cryo.tables.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("cryo.tenants.id")),
        sa.Column("epoch", sa.BigInteger(), nullable=False),
        sa.Column(
            "owner_intent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cryo.maintenance_intents.id", ondelete="SET NULL"),
        ),
        *_timestamps(),
        schema="cryo",
    )
    op.create_index(
        "ix_maintenance_fences_tenant_id", "maintenance_fences", ["tenant_id"], schema="cryo"
    )

    op.create_table(
        "maintenance_attempts",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "intent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cryo.maintenance_intents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.Text(), nullable=False),
        sa.Column("fencing_epoch", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("error", JSONB()),
        *_timestamps(),
        sa.UniqueConstraint("intent_id", "attempt_number", name="uq_attempts_intent_number"),
        schema="cryo",
    )
    op.create_index(
        "ix_maintenance_attempts_intent_id",
        "maintenance_attempts",
        ["intent_id"],
        schema="cryo",
    )
    op.create_index(
        "ix_maintenance_attempts_status_heartbeat",
        "maintenance_attempts",
        ["status", "heartbeat_at"],
        schema="cryo",
    )
    op.create_index(
        "uq_maintenance_attempts_active_intent",
        "maintenance_attempts",
        ["intent_id"],
        unique=True,
        schema="cryo",
        postgresql_where=sa.text("status = 'started'"),
    )

    op.create_table(
        "effect_receipts",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "intent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cryo.maintenance_intents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "attempt_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cryo.maintenance_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "destination_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cryo.destinations.id"),
            nullable=False,
        ),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("operation_type", sa.Text(), nullable=False),
        sa.Column("strategy", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("external_reference", sa.Text()),
        sa.Column("submission", JSONB()),
        sa.Column("result", JSONB()),
        sa.Column("error", JSONB()),
        sa.Column("submitted_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("probe_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_probe_at", sa.TIMESTAMP(timezone=True)),
        *_timestamps(),
        sa.UniqueConstraint("intent_id", "step_index", name="uq_effect_receipts_intent_step"),
        sa.UniqueConstraint("idempotency_key", name="uq_effect_receipts_idempotency_key"),
        schema="cryo",
    )
    op.create_index(
        "ix_effect_receipts_intent_id", "effect_receipts", ["intent_id"], schema="cryo"
    )
    op.create_index(
        "ix_effect_receipts_attempt_id", "effect_receipts", ["attempt_id"], schema="cryo"
    )
    op.create_index(
        "ix_effect_receipts_destination_id",
        "effect_receipts",
        ["destination_id"],
        schema="cryo",
    )
    op.create_index(
        "ix_effect_receipts_state_probe",
        "effect_receipts",
        ["state", "next_probe_at"],
        schema="cryo",
    )

    op.create_table(
        "maintenance_control_state",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reason", sa.Text()),
        sa.Column("changed_by", sa.Text(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("scope_type", "scope_id", name="uq_control_state_scope"),
        schema="cryo",
    )

    op.execute(
        """
        CREATE FUNCTION cryo.reject_maintenance_intent_rebinding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF ROW(
            NEW.tenant_id, NEW.job_id, NEW.parent_intent_id, NEW.version,
            NEW.actor_type, NEW.actor_id, NEW.source_id, NEW.table_id, NEW.table_ref,
            NEW.destination_id, NEW.operation_scope, NEW.observed_snapshot_id,
            NEW.policy_id, NEW.policy_version, NEW.canonical_plan, NEW.plan_hash,
            NEW.idempotency_key, NEW.approval_bindings, NEW.fencing_epoch, NEW.budget,
            NEW.capability_requirements, NEW.containment_action, NEW.expires_at
          ) IS DISTINCT FROM ROW(
            OLD.tenant_id, OLD.job_id, OLD.parent_intent_id, OLD.version,
            OLD.actor_type, OLD.actor_id, OLD.source_id, OLD.table_id, OLD.table_ref,
            OLD.destination_id, OLD.operation_scope, OLD.observed_snapshot_id,
            OLD.policy_id, OLD.policy_version, OLD.canonical_plan, OLD.plan_hash,
            OLD.idempotency_key, OLD.approval_bindings, OLD.fencing_epoch, OLD.budget,
            OLD.capability_requirements, OLD.containment_action, OLD.expires_at
          ) THEN
            RAISE EXCEPTION 'maintenance intent bound fields are immutable';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_maintenance_intents_immutable
        BEFORE UPDATE ON cryo.maintenance_intents
        FOR EACH ROW EXECUTE FUNCTION cryo.reject_maintenance_intent_rebinding();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_maintenance_intents_immutable ON cryo.maintenance_intents"
    )
    op.execute("DROP FUNCTION IF EXISTS cryo.reject_maintenance_intent_rebinding()")
    op.drop_table("maintenance_control_state", schema="cryo")
    op.drop_table("effect_receipts", schema="cryo")
    op.drop_table("maintenance_attempts", schema="cryo")
    op.drop_table("maintenance_fences", schema="cryo")
    op.drop_index("ix_jobs_current_intent_id", table_name="jobs", schema="cryo")
    op.drop_constraint(
        "fk_jobs_current_intent", "jobs", schema="cryo", type_="foreignkey"
    )
    op.drop_column("jobs", "intent_version", schema="cryo")
    op.drop_column("jobs", "current_intent_id", schema="cryo")
    op.drop_table("maintenance_intents", schema="cryo")
