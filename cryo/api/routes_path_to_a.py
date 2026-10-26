"""Path to A: what a table needs to reach grade A, and one-click steps toward it."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.advisor.partition_change import LayoutChangeError, change_partition_spec, set_sort_order
from cryo.advisor.path_to_a import (
    GIB,
    Step,
    TableSnapshot,
    build_path_to_a,
    commit_rate_from_history,
)
from cryo.api.deps import CurrentPrincipal, SessionDep, require_operator, require_viewer
from cryo.api.security import Principal, Role, require_role
from cryo.db.models import (
    AuditLog,
    LayoutRecommendation,
    Preset,
    QueryPattern,
    Table,
    TablePartition,
)
from cryo.db.tenant_scope import get_scoped_resource
from cryo.planner.partitions import partition_fields_from_stats

router = APIRouter(prefix="/api/v1", tags=["tables"])


class PathStepRead(BaseModel):
    id: str
    kind: Literal["automatic", "approval", "design", "external"]
    title: str
    detail: str
    fixes: list[str]
    projected_score: int
    projected_grade: str
    confidence: Literal["measured", "estimated", "assumed"]
    evidence: list[str] = Field(default_factory=list)
    action: dict[str, Any] | None = None
    required_role: str | None = None
    confirm_text: str | None = None
    in_schedule: bool = False


class PathToARead(BaseModel):
    table_id: uuid.UUID
    score: int
    grade: str
    deductions: list[dict[str, Any]]
    steps: list[PathStepRead]
    cryo_score: int
    cryo_grade: str
    reachable_score: int
    reachable_grade: str
    notes: list[str]
    # Partition count and skew findings: reported beside the grade, never deducted (#285).
    design_findings: list[dict[str, Any]] = Field(default_factory=list)


class PathToAApplyRequest(BaseModel):
    step_id: str = Field(min_length=1)
    confirm: str | None = None


class PathToAApplyResponse(BaseModel):
    step_id: str
    status: str
    message: str
    run_id: uuid.UUID | None = None
    audit_log_id: int | None = None


def _latest_partitions(session: Session, table_id: uuid.UUID) -> list[dict[str, Any]]:
    latest = session.scalar(
        select(func.max(TablePartition.scan_at)).where(TablePartition.table_id == table_id)
    )
    if latest is None:
        return []
    rows = session.scalars(
        select(TablePartition).where(
            TablePartition.table_id == table_id, TablePartition.scan_at == latest
        )
    )
    return [
        {
            "partition_value": row.partition_value,
            "partition_values": row.partition_values or {},
            "files_per_partition": row.files_per_partition,
            "small_files_in_partition_count": row.small_files_in_partition_count,
            "partition_size_gb": row.partition_size_gb,
        }
        for row in rows
    ]


def _query_filters(session: Session, table_id: uuid.UUID) -> dict[str, int]:
    rows = session.execute(
        select(QueryPattern.column_name, QueryPattern.filter_count).where(
            QueryPattern.table_id == table_id
        )
    )
    out: dict[str, int] = {}
    for column, count in rows:
        out[str(column)] = out.get(str(column), 0) + int(count or 0)
    return out


def _sort_columns(session: Session, table_id: uuid.UUID) -> list[str]:
    rec = session.scalars(
        select(LayoutRecommendation)
        .where(
            LayoutRecommendation.table_id == table_id,
            LayoutRecommendation.recommended_strategy != "none",
            LayoutRecommendation.status != "rejected",
        )
        .order_by(LayoutRecommendation.created_at.desc())
        .limit(1)
    ).first()
    if rec is None:
        return []
    columns: list[str] = []
    for item in rec.recommended_columns or []:
        column = item.get("column") if isinstance(item, Mapping) else item
        if isinstance(column, str) and column:
            columns.append(column)
    return columns


def _property_changes(session: Session, table_id: uuid.UUID) -> dict[str, str]:
    from cryo.scanner.property_recommendations import (
        TIER1_APPLYABLE_KEYS,
        compute_property_recommendations,
    )

    changes: dict[str, str] = {}
    for rec in compute_property_recommendations(session, table_id, include_bloom_filters=False):
        if (
            rec.action == "apply"
            and rec.recommended_value is not None
            and rec.property_key in TIER1_APPLYABLE_KEYS
            and not rec.needs_confirmation
        ):
            changes[rec.property_key] = rec.recommended_value
    return changes


def _commit_rate(session: Session, table_id: uuid.UUID, raw: Mapping[str, Any]) -> float | None:
    from cryo.scanner.persistence import stats_history_for_signals

    measured = float(raw.get("commits_per_day") or 0.0)
    if measured > 0:
        return measured
    return commit_rate_from_history(stats_history_for_signals(session, table_id))


def load_snapshot(session: Session, table: Table) -> TableSnapshot | None:
    from cryo.table_config.service import load_inputs

    inputs = load_inputs(session, table)
    stats = inputs.stats
    if stats is None:
        return None
    raw = dict(stats.raw or {})
    overrides = inputs.assignment.param_overrides if inputs.assignment else None
    params = {**inputs.preset_params, **(dict(overrides) if isinstance(overrides, Mapping) else {})}
    preset = session.get(Preset, inputs.preset_id) if inputs.preset_id else None
    fields = partition_fields_from_stats(
        {
            "raw": raw,
            "partition_col": stats.partition_col,
            "partition_col_type": stats.partition_col_type,
        },
        params,
    )
    return TableSnapshot(
        table_name=table.table_name,
        score=int(stats.overall_score),
        grade=str(stats.grade),
        deductions=list(raw.get("health_deductions") or []),
        total_files=int(stats.total_files or 0),
        total_partitions=int(stats.total_partitions or 0),
        total_size_bytes=int(float(stats.total_size_gb or 0) * GIB),
        total_snapshots=int(stats.total_snapshots or 0),
        manifest_count=int(stats.manifest_count or 0),
        commits_per_day=_commit_rate(session, table.id, raw),
        is_streaming=bool(raw.get("is_streaming")),
        last_commit_at=stats.last_snapshot_committed_at,
        table_target_bytes=stats.target_file_size_bytes,
        has_sort_order=bool(stats.has_sort_order),
        write_distribution_mode=stats.write_distribution_mode,
        params=params,
        scheduled_operations=frozenset(str(op) for op in (preset.operations if preset else [])),
        partition_fields=fields,
        partitions=_latest_partitions(session, table.id),
        partition_skew_ratio=float(stats.partition_skew_ratio or 0.0),
        query_filters=_query_filters(session, table.id),
        property_changes=_property_changes(session, table.id),
        sort_columns=_sort_columns(session, table.id),
        stats={
            "total_size_gb": float(stats.total_size_gb or 0),
            "total_files": int(stats.total_files or 0),
            "total_partitions": int(stats.total_partitions or 0),
            "avg_file_size_mb": float(stats.avg_file_size_mb or 0),
            "target_file_size_bytes": stats.target_file_size_bytes,
            "partition_skew_ratio": float(stats.partition_skew_ratio or 0.0),
            "commits_per_day": raw.get("commits_per_day"),
            "is_streaming": bool(raw.get("is_streaming")),
        },
    )


def _path(session: Session, table_id: uuid.UUID) -> tuple[Table, Any]:
    table = get_scoped_resource(session, Table, table_id, resource_type="Table")
    snapshot = load_snapshot(session, table)
    if snapshot is None:
        raise HTTPException(status_code=409, detail="Scan the table first")
    return table, build_path_to_a(snapshot)


@router.get(
    "/tables/{table_id}/path-to-a",
    response_model=PathToARead,
    dependencies=[Depends(require_viewer)],
)
def get_path_to_a(table_id: uuid.UUID, session: SessionDep) -> PathToARead:
    _, path = _path(session, table_id)
    return PathToARead(table_id=table_id, **path.to_dict())


@router.post(
    "/tables/{table_id}/path-to-a/apply",
    response_model=PathToAApplyResponse,
    dependencies=[Depends(require_operator)],
)
def apply_path_step(
    table_id: uuid.UUID,
    payload: PathToAApplyRequest,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> PathToAApplyResponse:
    """Apply one step. The server rebuilds the step from the latest scan; the client only
    names it, so what runs is always what the panel showed for the current scan."""
    table, path = _path(session, table_id)
    step = next((s for s in path.steps if s.id == payload.step_id), None)
    if step is None:
        raise HTTPException(
            status_code=409, detail="That step no longer applies to this table; reload the page"
        )
    if step.action is None:
        raise HTTPException(status_code=400, detail="This step has no action Cryo can take")
    require_role(principal, Role(step.required_role or Role.ADMIN.value))
    if step.confirm_text is not None and payload.confirm != step.confirm_text:
        raise HTTPException(
            status_code=400, detail=f"Type {step.confirm_text} to confirm this change"
        )
    kind = step.action["type"]
    if kind == "run_operations":
        return _run_operations(table_id, step, session, principal)
    if kind == "set_overrides":
        return _set_overrides(table, step, session, principal)
    if kind == "apply_properties":
        return _apply_properties(table_id, step, session, principal)
    if kind in {"partition_spec", "sort_order"}:
        return _change_layout(table_id, step, session, principal)
    raise HTTPException(status_code=400, detail=f"Unknown step action: {kind}")


def _run_operations(
    table_id: uuid.UUID, step: Step, session: Session, principal: Principal
) -> PathToAApplyResponse:
    from cryo.api.routes_table_maintenance import TableOperationRunRequest, run_table_operations
    from cryo.domain.enums import OperationType

    assert step.action is not None
    request = TableOperationRunRequest(
        operations=[OperationType(op) for op in step.action["operations"]],
        trigger="path-to-a",
    )
    run = run_table_operations(table_id, request, session, principal)
    return PathToAApplyResponse(
        step_id=step.id,
        status="started",
        message=f"Started: {step.title}.",
        run_id=run.run_id,
    )


def _audit(
    session: Session, table_id: uuid.UUID, action: str, detail: str, principal: Principal
) -> int:
    audit = AuditLog(
        table_id=table_id,
        action=action,
        status="OK",
        engine="cryo",
        detail=detail,
        actor=principal.subject,
    )
    session.add(audit)
    session.commit()
    session.refresh(audit)
    return int(audit.id)


def _set_overrides(
    table: Table, step: Step, session: Session, principal: Principal
) -> PathToAApplyResponse:
    from cryo.api.routes import patch_table_param_overrides
    from cryo.api.schemas import TableParamOverridePatch
    from cryo.db.models import TableAssignment

    assert step.action is not None
    assignment = session.get(TableAssignment, table.id)
    current = dict(assignment.param_overrides or {}) if assignment else {}
    merged = {**current, **step.action["overrides"]}
    patch_table_param_overrides(table.id, TableParamOverridePatch(param_overrides=merged), session)
    changes = " ".join(f"{k}={v}" for k, v in sorted(step.action["overrides"].items()))
    audit_id = _audit(session, table.id, "path_to_a_override", changes, principal)
    return PathToAApplyResponse(
        step_id=step.id,
        status="applied",
        message=f"Saved for this table: {changes}. Cryo applies it on every run.",
        audit_log_id=audit_id,
    )


def _apply_properties(
    table_id: uuid.UUID, step: Step, session: Session, principal: Principal
) -> PathToAApplyResponse:
    from cryo.api.routes_property_recommendations import (
        ApplyTablePropertiesRequest,
        apply_table_properties_endpoint,
    )

    assert step.action is not None
    result = apply_table_properties_endpoint(
        table_id,
        ApplyTablePropertiesRequest(properties=step.action["properties"], note="path-to-a"),
        session,
        principal,
    )
    return PathToAApplyResponse(
        step_id=step.id,
        status="applied",
        message=result.message,
        audit_log_id=result.audit_log_id,
    )


def _change_layout(
    table_id: uuid.UUID, step: Step, session: Session, principal: Principal
) -> PathToAApplyResponse:
    action = step.action
    assert action is not None
    try:
        if action["type"] == "partition_spec":
            result = change_partition_spec(
                session, table_id, remove=action["remove"], add=action["add"]
            )
            audit_action = "partition_spec_change"
            removed = ", ".join(result["removed"]) or "nothing"
            added = ", ".join(result["added"]) or "nothing"
            detail = f"removed={removed} added={added} spec_id={result['spec_id']}"
            message = (
                f"Partition spec changed (removed {removed}; added {added}). New writes use it "
                "now; existing data moves over as it is rewritten. Rescan to update the grade."
            )
        else:
            result = set_sort_order(session, table_id, action["columns"])
            audit_action = "sort_order_change"
            detail = f"sort_order={','.join(result['sort_order'])}"
            message = f"Sort order set to {', '.join(result['sort_order'])}."
    except LayoutChangeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        _audit_failure(session, table_id, step, exc, principal)
        raise HTTPException(status_code=502, detail=f"Catalog commit failed: {exc}") from exc
    audit_id = _audit(session, table_id, audit_action, detail, principal)
    return PathToAApplyResponse(
        step_id=step.id, status="applied", message=message, audit_log_id=audit_id
    )


def _audit_failure(
    session: Session, table_id: uuid.UUID, step: Step, exc: Exception, principal: Principal
) -> None:
    session.rollback()
    session.add(
        AuditLog(
            table_id=table_id,
            action=f"{step.action['type'] if step.action else 'layout'}_change",
            status="FAILED",
            engine="pyiceberg",
            detail=str(exc)[:500],
            actor=principal.subject,
        )
    )
    session.commit()
