from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from cryo.capabilities.models import EffectStrategy, MaturityTier
from cryo.domain.enums import EngineType, OperationType


class IntentStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    READY = "ready"
    EXECUTING = "executing"
    RECONCILE_REQUIRED = "reconcile_required"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class AttemptStatus(StrEnum):
    STARTED = "started"
    DEFERRED = "deferred"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RECONCILE_REQUIRED = "reconcile_required"
    FENCED = "fenced"


class EffectState(StrEnum):
    PREPARED = "prepared"
    SUBMITTED = "submitted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ContainmentAction(StrEnum):
    REPLAN = "replan"
    RECONCILE = "reconcile"
    MANUAL_REVIEW = "manual_review"
    ABORT = "abort"


class FrozenIntentModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class IntentBudget(FrozenIntentModel):
    max_input_bytes: int | None = Field(default=None, ge=0)
    max_output_bytes: int | None = Field(default=None, ge=0)
    max_input_files: int | None = Field(default=None, ge=0)
    max_output_files: int | None = Field(default=None, ge=0)
    max_partitions: int | None = Field(default=None, ge=0)
    max_duration_seconds: int | None = Field(default=None, ge=1)
    max_objects_deleted: int | None = Field(default=None, ge=0)


@dataclass(frozen=True, slots=True)
class EffectBudgetUsage:
    output_bytes: int = 0
    output_files: int = 0
    duration_ms: int = 0
    objects_deleted: int = 0

    def add(
        self,
        *,
        output_bytes: int | None = None,
        output_files: int | None = None,
        duration_ms: int | None = None,
        objects_deleted: int | None = None,
    ) -> EffectBudgetUsage:
        return EffectBudgetUsage(
            output_bytes=self.output_bytes + max(0, output_bytes or 0),
            output_files=self.output_files + max(0, output_files or 0),
            duration_ms=self.duration_ms + max(0, duration_ms or 0),
            objects_deleted=self.objects_deleted + max(0, objects_deleted or 0),
        )


class ApprovalBinding(FrozenIntentModel):
    approval_id: UUID
    approved_by: str = Field(min_length=1)
    reason: str | None = None
    approved_at: datetime
    expires_at: datetime
    operation: OperationType
    params_hash: str = Field(min_length=64, max_length=64)


class CapabilityRequirement(FrozenIntentModel):
    destination_id: UUID
    engine: EngineType
    operation: OperationType
    minimum_maturity: MaturityTier
    effect_strategy: EffectStrategy
    executes_effect: bool = True


class IntentBinding(FrozenIntentModel):
    tenant_id: UUID | None = None
    actor_type: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    source_id: UUID
    table_id: UUID
    table_ref: str = Field(min_length=1)
    destination_id: UUID
    operation_scope: tuple[OperationType, ...]
    observed_snapshot_id: int | None = None
    policy_id: str | None = None
    policy_version: str | None = None
    canonical_plan: dict[str, Any]
    plan_hash: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=255)
    expires_at: datetime
    budget: IntentBudget
    capability_requirements: tuple[CapabilityRequirement, ...]
    containment_action: ContainmentAction
    approvals: tuple[ApprovalBinding, ...] = ()


class MaintenanceIntentRecord(IntentBinding):
    id: UUID
    job_id: UUID
    version: int = Field(ge=1)
    parent_intent_id: UUID | None = None
    status: IntentStatus
    fencing_epoch: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
