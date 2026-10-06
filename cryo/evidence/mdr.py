from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.capabilities import capability_for
from cryo.db.models import (
    AuditLog,
    Destination,
    EffectReceipt,
    MaintenanceAttempt,
    MaintenanceIntent,
    MaintenanceJob,
)
from cryo.domain.enums import EngineType, OperationType

MDR_SCHEMA_VERSION = "cryo.maintenance-decision-record/v1"

_OUTCOME_FIELDS = (
    "duration_ms",
    "actual_bytes_freed",
    "input_files_count",
    "output_files_count",
)
_RESULT_METRIC_FIELDS = (
    "input_bytes",
    "output_bytes",
    "input_files",
    "output_files",
    "duration_ms",
    "objects_deleted",
)


def build_maintenance_decision_record(
    session: Session,
    intent_id: uuid.UUID,
) -> dict[str, Any]:
    intent = session.get(MaintenanceIntent, intent_id)
    if intent is None:
        raise ValueError(f"Maintenance intent not found: {intent_id}")
    job = session.get(MaintenanceJob, intent.job_id)
    destination = session.get(Destination, intent.destination_id)
    if job is None or destination is None:
        raise ValueError(f"Maintenance intent dependencies are incomplete: {intent_id}")

    attempts = list(
        session.scalars(
            select(MaintenanceAttempt)
            .where(MaintenanceAttempt.intent_id == intent.id)
            .order_by(MaintenanceAttempt.attempt_number, MaintenanceAttempt.id)
        )
    )
    effects = list(
        session.scalars(
            select(EffectReceipt)
            .where(EffectReceipt.intent_id == intent.id)
            .order_by(EffectReceipt.step_index, EffectReceipt.id)
        )
    )
    outcomes = list(
        session.scalars(
            select(AuditLog).where(AuditLog.job_id == job.id).order_by(AuditLog.ts, AuditLog.id)
        )
    )
    engine = EngineType(destination.engine)
    capabilities = [
        _capability_payload(engine, OperationType(operation))
        for operation in intent.operation_scope
    ]
    plan = _redacted_plan(intent.canonical_plan)
    record_id = _sha256(f"{intent.id}:{intent.plan_hash}")
    record: dict[str, Any] = {
        "schema_version": MDR_SCHEMA_VERSION,
        "record_id": record_id,
        "privacy": {
            "mode": "redacted",
            "actor_id_sha256": _sha256(intent.actor_id),
            "table_ref_sha256": _sha256(intent.table_ref),
            "omitted_fields": [
                "actor_id",
                "table_ref",
                "effect_submission",
                "effect_result_unbounded",
                "audit_detail",
                "query_text",
                "secrets",
            ],
        },
        "decision": {
            "intent_id": str(intent.id),
            "job_id": str(intent.job_id),
            "tenant_id": str(intent.tenant_id) if intent.tenant_id is not None else None,
            "table_id": str(intent.table_id),
            "destination_id": str(intent.destination_id),
            "actor_type": intent.actor_type,
            "status": intent.status,
            "version": intent.version,
            "parent_intent_id": (
                str(intent.parent_intent_id) if intent.parent_intent_id is not None else None
            ),
            "observed_snapshot_id": intent.observed_snapshot_id,
            "policy": {
                "id": intent.policy_id,
                "version": intent.policy_version,
            },
            "approval_bindings": _redacted_approvals(intent.approval_bindings),
            "expires_at": _iso(intent.expires_at),
            "containment_action": intent.containment_action,
        },
        "plan": {
            "hash": intent.plan_hash,
            "canonical_redacted": plan,
            "operation_scope": list(intent.operation_scope),
            "budget": intent.budget,
            "capability_requirements": intent.capability_requirements,
            "fencing_epoch": intent.fencing_epoch,
        },
        "attempts": [
            {
                "attempt_id": str(attempt.id),
                "number": attempt.attempt_number,
                "status": attempt.status,
                "fencing_epoch": attempt.fencing_epoch,
                "started_at": _iso(attempt.started_at),
                "completed_at": _iso(attempt.completed_at),
                "error_class": _error_class(attempt.error),
            }
            for attempt in attempts
        ],
        "effects": [
            {
                "effect_id": str(effect.id),
                "attempt_id": str(effect.attempt_id),
                "step_index": effect.step_index,
                "operation": effect.operation_type,
                "strategy": effect.strategy,
                "state": effect.state,
                "external_reference_sha256": (
                    _sha256(effect.external_reference)
                    if effect.external_reference is not None
                    else None
                ),
                "submitted_at": _iso(effect.submitted_at),
                "resolved_at": _iso(effect.resolved_at),
                "probe_count": effect.probe_count,
                "result_metrics": _bounded_metrics(effect.result),
                "error_class": _error_class(effect.error),
            }
            for effect in effects
        ],
        "outcomes": [
            {
                "audit_id": audit.id,
                "at": _iso(audit.ts),
                "action": audit.action,
                "status": audit.status,
                "engine": audit.engine,
                **{field: getattr(audit, field) for field in _OUTCOME_FIELDS},
            }
            for audit in outcomes
        ],
        "capabilities": capabilities,
    }
    record["integrity"] = {
        "record_sha256": _sha256(_canonical_json(record)),
        "canonicalization": "sorted compact JSON",
    }
    return record


def _capability_payload(engine: EngineType, operation: OperationType) -> dict[str, Any]:
    capability = capability_for(engine, operation)
    return {
        "engine": engine.value,
        "operation": operation.value,
        "support": capability.support.value,
        "maturity": capability.maturity.value,
        "execution_mode": capability.execution_mode.value,
        "effect_strategy": capability.effect_strategy.value,
        "runtime_conditions": list(capability.runtime_conditions),
        "limitations": list(capability.limitations),
        "evidence": [
            {
                "kind": evidence.kind,
                "path": evidence.path,
                "symbol": evidence.symbol,
            }
            for evidence in capability.evidence
        ],
    }


def _redacted_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    plan = json.loads(json.dumps(value, default=str))
    if isinstance(plan, dict) and "table_ref" in plan:
        plan["table_ref"] = f"sha256:{_sha256(str(plan['table_ref']))}"
    operations = plan.get("operations") if isinstance(plan, dict) else None
    if isinstance(operations, list):
        for operation in operations:
            if not isinstance(operation, dict):
                continue
            predicates = operation.get("predicates")
            if not isinstance(predicates, list):
                continue
            for predicate in predicates:
                if not isinstance(predicate, dict):
                    continue
                terms = predicate.get("terms")
                if not isinstance(terms, list):
                    continue
                for term in terms:
                    if isinstance(term, dict) and "value" in term:
                        term["value"] = f"sha256:{_sha256(str(term['value']))}"
    return plan if isinstance(plan, dict) else {}


def _redacted_approvals(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for approval in value:
        if not isinstance(approval, Mapping):
            continue
        result.append(
            {
                "approval_id": str(approval.get("approval_id") or ""),
                "approved_by_sha256": _sha256(str(approval.get("approved_by") or "")),
                "approved_at": str(approval.get("approved_at") or ""),
                "expires_at": str(approval.get("expires_at") or ""),
                "operation": str(approval.get("operation") or ""),
                "params_hash": str(approval.get("params_hash") or ""),
            }
        )
    return result


def _bounded_metrics(value: object) -> dict[str, int | float]:
    if not isinstance(value, Mapping):
        return {}
    return {
        field: metric
        for field in _RESULT_METRIC_FIELDS
        if isinstance((metric := value.get(field)), int | float) and not isinstance(metric, bool)
    }


def _error_class(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    for key in ("class", "type", "code"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate[:128]
    return "unspecified"


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
