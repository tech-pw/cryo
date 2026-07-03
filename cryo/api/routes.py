from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql.elements import ColumnElement

from cryo.api.deps import require_admin, require_operator, require_viewer
from cryo.api.predicates import compile_predicate
from cryo.api.schemas import (
    AssignmentDeleteRequest,
    AssignmentRequest,
    AssignmentResponse,
    AssignmentSelection,
    CostDashboardResponse,
    DatalakeDashboardResponse,
    DestinationCreate,
    DestinationPatch,
    DestinationRead,
    EffectivePresetResponse,
    JobRead,
    MaintenancePreviewRequest,
    MaintenancePreviewResponse,
    MaintenanceRunRequest,
    MaintenanceRunResponse,
    PaginatedTables,
    PresetCreate,
    PresetPatch,
    PresetRead,
    PresetRuleCreate,
    PresetRuleRead,
    PresetSampleTable,
    PresetSimulationResponse,
    RenderedStatementRead,
    RunJobsResponse,
    RunRead,
    RunSnapshotPayload,
    ScanTriggerRequest,
    ScanTriggerResponse,
    SchemaCostDashboardResponse,
    SourceCreate,
    SourcePatch,
    SourceRead,
    TableAuditResponse,
    TableCostRead,
    TablePartitionsResponse,
    TablePreviewRead,
    TableRead,
    TableStatsHistoryResponse,
)
from cryo.api.security import Role, principal_from_token
from cryo.api.serializers import table_to_read
from cryo.config import Settings
from cryo.db.models import (
    Destination,
    MaintenanceJob,
    MaintenanceRun,
    Preset,
    PresetRule,
    Source,
    Table,
    TableCost,
    TableStats,
)
from cryo.db.repositories import p4, p5
from cryo.db.session import get_session
from cryo.domain.enums import EngineType, JobStatus
from cryo.domain.models import MaintenancePlan
from cryo.engines.base import RenderedStatement
from cryo.engines.spark.sql import render as render_spark
from cryo.engines.trino.engine import TrinoEngine
from cryo.engines.trino.sql import render as render_trino
from cryo.planner.planner import build_plan
from cryo.planner.guardrails import validate_operation_order

SessionDep = Annotated[Session, Depends(get_session)]

router = APIRouter(prefix="/api/v1")

SORT_FIELDS = {
    "schema_name": Table.schema_name,
    "table_name": Table.table_name,
    "total_size_gb": TableStats.total_size_gb,
    "small_files_count": TableStats.small_files_count,
    "total_files": TableStats.total_files,
    "grade": TableStats.grade,
    "overall_score": TableStats.overall_score,
    "compaction_priority": TableStats.compaction_priority,
    "projected_savings_usd_month": TableStats.projected_savings_usd_month,
    "projected_monthly_s3_cost_usd": TableStats.projected_monthly_s3_cost_usd,
    "reclaimable_bytes": TableStats.reclaimable_bytes,
}


@router.get(
    "/dashboards/datalake",
    response_model=DatalakeDashboardResponse,
    dependencies=[Depends(require_viewer)],
    tags=["dashboards"],
)
def get_datalake_dashboard(
    session: SessionDep,
    top_limit: Annotated[int, Query(ge=1, le=50)] = 10,
    drift_limit: Annotated[int, Query(ge=1, le=365)] = 30,
) -> dict[str, object]:
    return p5.dashboard_datalake(session, top_limit=top_limit, drift_limit=drift_limit)


@router.get(
    "/dashboards/schema-cost",
    response_model=SchemaCostDashboardResponse,
    dependencies=[Depends(require_viewer)],
    tags=["dashboards"],
)
def get_schema_cost_dashboard(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, object]:
    return p5.dashboard_schema_cost(session, limit=limit)


@router.get(
    "/dashboards/cost",
    response_model=CostDashboardResponse,
    dependencies=[Depends(require_viewer)],
    tags=["dashboards"],
)
def get_cost_dashboard(
    session: SessionDep,
    top_limit: Annotated[int, Query(ge=1, le=50)] = 10,
    trend_limit: Annotated[int, Query(ge=1, le=365)] = 30,
) -> dict[str, object]:
    return p5.dashboard_cost(
        session,
        top_limit=top_limit,
        trend_limit=trend_limit,
        settings=Settings(),
    )


@router.get(
    "/tables/{table_id}/cost",
    response_model=TableCostRead,
    dependencies=[Depends(require_viewer)],
    tags=["tables"],
)
def get_table_cost(table_id: uuid.UUID, session: SessionDep) -> dict[str, object]:
    payload = p5.table_cost(session, table_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Table cost not found")
    return payload


@router.get(
    "/sources",
    response_model=list[SourceRead],
    dependencies=[Depends(require_viewer)],
)
def list_sources(session: SessionDep) -> list[Source]:
    return list(session.scalars(select(Source).order_by(Source.name)))


@router.get(
    "/sources/{source_id}",
    response_model=SourceRead,
    dependencies=[Depends(require_viewer)],
)
def get_source(source_id: uuid.UUID, session: SessionDep) -> Source:
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


@router.post(
    "/sources",
    response_model=SourceRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
def create_source(payload: SourceCreate, session: SessionDep) -> Source:
    source = Source(**payload.model_dump())
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


@router.patch(
    "/sources/{source_id}",
    response_model=SourceRead,
    dependencies=[Depends(require_admin)],
)
def patch_source(source_id: uuid.UUID, payload: SourcePatch, session: SessionDep) -> Source:
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    _apply_patch(source, payload.model_dump(exclude_unset=True))
    session.commit()
    session.refresh(source)
    return source


@router.delete(
    "/sources/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
def delete_source(source_id: uuid.UUID, session: SessionDep) -> None:
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    source.enabled = False
    session.commit()


@router.get(
    "/destinations",
    response_model=list[DestinationRead],
    dependencies=[Depends(require_viewer)],
)
def list_destinations(session: SessionDep) -> list[Destination]:
    return list(session.scalars(select(Destination).order_by(Destination.name)))


@router.get(
    "/destinations/{destination_id}",
    response_model=DestinationRead,
    dependencies=[Depends(require_viewer)],
)
def get_destination(destination_id: uuid.UUID, session: SessionDep) -> Destination:
    destination = session.get(Destination, destination_id)
    if destination is None:
        raise HTTPException(status_code=404, detail="Destination not found")
    return destination


@router.post(
    "/destinations",
    response_model=DestinationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
def create_destination(payload: DestinationCreate, session: SessionDep) -> Destination:
    destination = Destination(**payload.model_dump(mode="json"))
    session.add(destination)
    session.commit()
    session.refresh(destination)
    return destination


@router.patch(
    "/destinations/{destination_id}",
    response_model=DestinationRead,
    dependencies=[Depends(require_admin)],
)
def patch_destination(
    destination_id: uuid.UUID, payload: DestinationPatch, session: SessionDep
) -> Destination:
    destination = session.get(Destination, destination_id)
    if destination is None:
        raise HTTPException(status_code=404, detail="Destination not found")
    _apply_patch(destination, payload.model_dump(exclude_unset=True, mode="json"))
    session.commit()
    session.refresh(destination)
    return destination


@router.delete(
    "/destinations/{destination_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
def delete_destination(destination_id: uuid.UUID, session: SessionDep) -> None:
    destination = session.get(Destination, destination_id)
    if destination is None:
        raise HTTPException(status_code=404, detail="Destination not found")
    destination.enabled = False
    session.commit()


@router.post(
    "/destinations/{destination_id}/test",
    dependencies=[Depends(require_operator)],
)
def test_destination(destination_id: uuid.UUID, session: SessionDep) -> dict[str, object]:
    destination = session.get(Destination, destination_id)
    if destination is None:
        raise HTTPException(status_code=404, detail="Destination not found")
    if destination.engine == EngineType.TRINO.value:
        try:
            snapshot = TrinoEngine(dict(destination.config)).probe_capacity()
            return {
                "ok": destination.enabled,
                "engine": destination.engine,
                "name": destination.name,
                "capacity": snapshot.capacity,
                "used": snapshot.used,
                "available": snapshot.available,
            }
        except Exception as exc:
            return {
                "ok": False,
                "engine": destination.engine,
                "name": destination.name,
                "message": str(exc),
            }
    return {
        "ok": bool(destination.enabled),
        "engine": destination.engine,
        "name": destination.name,
        "message": (
            "Configuration is present; live engine probe is implemented in the governor/worker."
        ),
    }


@router.get(
    "/presets",
    response_model=list[PresetRead],
    dependencies=[Depends(require_viewer)],
)
def list_presets(session: SessionDep) -> list[Preset]:
    return list(session.scalars(select(Preset).order_by(Preset.name)))


@router.get(
    "/presets/{preset_id}",
    response_model=PresetRead,
    dependencies=[Depends(require_viewer)],
)
def get_preset(preset_id: uuid.UUID, session: SessionDep) -> Preset:
    preset = session.get(Preset, preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="Preset not found")
    return preset


@router.post(
    "/presets",
    response_model=PresetRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
def create_preset(payload: PresetCreate, session: SessionDep) -> Preset:
    if session.get(Destination, payload.destination_id) is None:
        raise HTTPException(status_code=400, detail="Destination does not exist")
    values = payload.model_dump()
    values["mode"] = payload.mode.value
    preset = Preset(**values)
    session.add(preset)
    session.commit()
    session.refresh(preset)
    return preset


@router.patch(
    "/presets/{preset_id}",
    response_model=PresetRead,
    dependencies=[Depends(require_admin)],
)
def patch_preset(preset_id: uuid.UUID, payload: PresetPatch, session: SessionDep) -> Preset:
    preset = session.get(Preset, preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="Preset not found")
    values = payload.model_dump(exclude_unset=True)
    if payload.mode is not None:
        values["mode"] = payload.mode.value
    destination_id = values.get("destination_id")
    if destination_id is not None and session.get(Destination, destination_id) is None:
        raise HTTPException(status_code=400, detail="Destination does not exist")
    _apply_patch(preset, values)
    session.commit()
    session.refresh(preset)
    return preset


@router.delete(
    "/presets/{preset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
def delete_preset(preset_id: uuid.UUID, session: SessionDep) -> None:
    preset = session.get(Preset, preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="Preset not found")
    preset.enabled = False
    session.commit()


@router.get(
    "/presets/{preset_id}/rules",
    response_model=list[PresetRuleRead],
    dependencies=[Depends(require_viewer)],
)
def list_preset_rules(preset_id: uuid.UUID, session: SessionDep) -> list[PresetRule]:
    if session.get(Preset, preset_id) is None:
        raise HTTPException(status_code=404, detail="Preset not found")
    return list(session.scalars(select(PresetRule).where(PresetRule.preset_id == preset_id)))


@router.post(
    "/presets/{preset_id}/rules",
    response_model=PresetRuleRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
def create_preset_rule(
    preset_id: uuid.UUID, payload: PresetRuleCreate, session: SessionDep
) -> PresetRule:
    if session.get(Preset, preset_id) is None:
        raise HTTPException(status_code=404, detail="Preset not found")
    _validate_filter(payload.predicate)
    rule = PresetRule(
        preset_id=preset_id,
        predicate=payload.predicate,
        enabled=payload.enabled,
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


@router.delete(
    "/presets/{preset_id}/rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
def delete_preset_rule(preset_id: uuid.UUID, rule_id: uuid.UUID, session: SessionDep) -> None:
    rule = session.get(PresetRule, rule_id)
    if rule is None or rule.preset_id != preset_id:
        raise HTTPException(status_code=404, detail="Preset rule not found")
    session.delete(rule)
    session.commit()


@router.post(
    "/presets/{preset_id}/simulate",
    response_model=PresetSimulationResponse,
    dependencies=[Depends(require_operator)],
)
def simulate_preset(preset_id: uuid.UUID, session: SessionDep) -> PresetSimulationResponse:
    preset = session.get(Preset, preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="Preset not found")
    sample, estimate = p4.simulate_preset(session, preset)
    return PresetSimulationResponse(
        preset_id=preset.id,
        matched_count=int(estimate["matched_count"]),
        total_size_gb=float(estimate["total_size_gb"]),
        small_files_count=int(estimate["small_files_count"]),
        sample=[
            PresetSampleTable(
                id=uuid.UUID(table.id),
                schema_name=table.schema_name,
                table_name=table.table_name,
                total_size_gb=float(table.stats.get("total_size_gb") or 0),
                small_files_count=int(table.stats.get("small_files_count") or 0),
                grade=str(table.stats["grade"]) if table.stats.get("grade") else None,
            )
            for table in sample
        ],
    )


@router.get(
    "/tables",
    response_model=PaginatedTables,
    dependencies=[Depends(require_viewer)],
)
def list_tables(
    session: SessionDep,
    filter: Annotated[str | None, Query()] = None,
    sort: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginatedTables:
    predicate = _parse_filter(filter)
    stmt = (
        select(Table)
        .outerjoin(TableStats)
        .outerjoin(TableCost)
        .options(selectinload(Table.stats), selectinload(Table.cost))
    )
    count_stmt = select(func.count()).select_from(Table).outerjoin(TableStats).outerjoin(TableCost)
    if predicate is not None:
        stmt = stmt.where(predicate)
        count_stmt = count_stmt.where(predicate)

    total = session.scalar(count_stmt) or 0
    items = list(session.scalars(stmt.order_by(*_sort_clauses(sort)).offset(offset).limit(limit)))
    return PaginatedTables(
        items=[table_to_read(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/tables/{table_id}",
    response_model=TableRead,
    dependencies=[Depends(require_viewer)],
)
def get_table(table_id: uuid.UUID, session: SessionDep) -> TableRead:
    table = session.scalar(
        select(Table)
        .where(Table.id == table_id)
        .options(
            selectinload(Table.stats),
            selectinload(Table.partitions),
            selectinload(Table.cost),
        )
    )
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")
    return table_to_read(table)


@router.get(
    "/tables/{table_id}/partitions",
    response_model=TablePartitionsResponse,
    dependencies=[Depends(require_viewer)],
    tags=["tables"],
)
def get_table_partitions(
    table_id: uuid.UUID,
    session: SessionDep,
    scan_at: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    result = p5.table_partitions(
        session,
        table_id,
        scan_at=scan_at,
        limit=limit,
        offset=offset,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Table not found")
    return result


@router.get(
    "/tables/{table_id}/audit",
    response_model=TableAuditResponse,
    dependencies=[Depends(require_viewer)],
    tags=["tables"],
)
def get_table_audit(
    table_id: uuid.UUID,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    result = p5.table_audit(session, table_id, limit=limit, offset=offset)
    if result is None:
        raise HTTPException(status_code=404, detail="Table not found")
    return result


@router.get(
    "/tables/{table_id}/history",
    response_model=TableStatsHistoryResponse,
    dependencies=[Depends(require_viewer)],
    tags=["tables"],
)
def get_table_history(
    table_id: uuid.UUID,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=365)] = 90,
) -> dict[str, object]:
    result = p5.table_history(session, table_id, limit=limit)
    if result is None:
        raise HTTPException(status_code=404, detail="Table not found")
    return result


@router.get(
    "/tables/{table_id}/effective-preset",
    response_model=EffectivePresetResponse,
    dependencies=[Depends(require_viewer)],
)
def get_effective_preset(table_id: uuid.UUID, session: SessionDep) -> EffectivePresetResponse:
    table = p4.get_table_with_stats(session, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")
    effective = p4.resolve_effective_preset(session, table)
    if effective is None:
        return EffectivePresetResponse(table_id=table_id, provenance="none")
    provenance = cast(Literal["assignment", "rule", "none"], effective.provenance)
    return EffectivePresetResponse(
        table_id=table_id,
        preset_id=uuid.UUID(effective.preset.id),
        preset_name=effective.preset.name,
        provenance=provenance,
        rule_id=uuid.UUID(effective.rule_id) if effective.rule_id else None,
    )


@router.post(
    "/assignments",
    response_model=AssignmentResponse,
    dependencies=[Depends(require_operator)],
)
def assign_tables(payload: AssignmentRequest, session: SessionDep) -> AssignmentResponse:
    if session.get(Preset, payload.preset_id) is None:
        raise HTTPException(status_code=400, detail="Preset does not exist")

    selection = _resolve_selection(session, payload.selection)
    affected = p4.assign_tables(
        session,
        table_ids=selection.table_ids,
        preset_id=payload.preset_id,
        assigned_by=payload.assigned_by,
    )
    session.commit()
    return AssignmentResponse(affected=affected)


@router.delete(
    "/assignments",
    response_model=AssignmentResponse,
    dependencies=[Depends(require_operator)],
)
def unassign_tables(payload: AssignmentDeleteRequest, session: SessionDep) -> AssignmentResponse:
    selection = _resolve_selection(session, payload.selection)
    affected = p4.unassign_tables(session, table_ids=selection.table_ids)
    session.commit()
    return AssignmentResponse(affected=affected)


@router.post(
    "/maintenance/preview",
    response_model=MaintenancePreviewResponse,
    dependencies=[Depends(require_operator)],
)
def preview_maintenance(payload: MaintenancePreviewRequest) -> MaintenancePreviewResponse:
    requested_ops = payload.params.get("operations")
    if requested_ops:
        warnings = validate_operation_order(requested_ops)
        if warnings:
            raise HTTPException(status_code=400, detail={"warnings": warnings})

    renderer = _renderer_for(payload.engine)
    items: list[TablePreviewRead] = []

    for table in payload.tables:
        stats = {
            **table.stats,
            "catalog": table.catalog,
            "schema_name": table.schema_name,
            "table_name": table.table_name,
        }
        plan = build_plan(stats, table.partitions, payload.mode, payload.params)
        statements = [
            RenderedStatementRead(
                action=statement.action.value,
                engine=statement.engine,
                sql=statement.sql,
            )
            for statement in renderer(plan)
        ]
        items.append(
            TablePreviewRead(
                table_ref=plan.table_ref,
                notes=list(plan.notes),
                statements=statements,
            )
        )
    return MaintenancePreviewResponse(items=items)


@router.post(
    "/maintenance/run",
    response_model=MaintenanceRunResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_operator)],
)
def create_maintenance_run(
    payload: MaintenanceRunRequest, session: SessionDep
) -> MaintenanceRunResponse:
    if session.get(Destination, payload.destination_id) is None:
        raise HTTPException(status_code=400, detail="Destination does not exist")
    if payload.preset_id and session.get(Preset, payload.preset_id) is None:
        raise HTTPException(status_code=400, detail="Preset does not exist")

    selection = (
        _resolve_selection(session, payload.selection)
        if payload.selection is not None
        else _resolve_selection(
            session,
            AssignmentSelection(mode="ids", ids=payload.table_ids, filter=None),
        )
    )

    run = MaintenanceRun(
        kind="adhoc",
        preset_id=payload.preset_id,
        trigger=payload.trigger,
        status="queued",
    )
    session.add(run)
    session.flush()

    jobs: list[MaintenanceJob] = []
    for table_id in selection.table_ids:
        job = MaintenanceJob(
            run_id=run.id,
            table_id=table_id,
            destination_id=payload.destination_id,
            mode=payload.mode.value,
            status="queued",
        )
        session.add(job)
        jobs.append(job)
    session.commit()
    for job in jobs:
        session.refresh(job)
    return MaintenanceRunResponse(
        run_id=run.id, job_ids=[job.id for job in jobs], status=run.status
    )


@router.post(
    "/scans",
    response_model=ScanTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_operator)],
    tags=["scans"],
)
def create_scan_trigger(
    payload: ScanTriggerRequest,
    session: SessionDep,
) -> dict[str, object]:
    try:
        return p5.create_scan_trigger(session, payload.model_dump(mode="python"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/runs",
    response_model=list[RunRead],
    dependencies=[Depends(require_viewer)],
    tags=["runs"],
)
def list_runs(session: SessionDep) -> list[RunRead]:
    runs = session.scalars(
        select(MaintenanceRun).order_by(MaintenanceRun.created_at.desc()).limit(100)
    )
    return [RunRead.model_validate(p4.run_summary(run)) for run in runs]


@router.get(
    "/runs/{run_id}",
    response_model=RunRead,
    dependencies=[Depends(require_viewer)],
    tags=["runs"],
)
def get_run(run_id: uuid.UUID, session: SessionDep) -> RunRead:
    run = session.get(MaintenanceRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunRead.model_validate(p4.run_summary(run))


@router.get(
    "/runs/{run_id}/jobs",
    response_model=RunJobsResponse,
    dependencies=[Depends(require_viewer)],
    tags=["runs"],
)
def get_run_jobs(run_id: uuid.UUID, session: SessionDep) -> dict[str, object]:
    snapshot = p5.run_jobs_snapshot(session, run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return snapshot


@router.websocket("/ws/runs/{run_id}")
async def watch_run(
    websocket: WebSocket,
    run_id: uuid.UUID,
    session: SessionDep,
    token: Annotated[str | None, Query()] = None,
    once: Annotated[bool, Query()] = False,
    poll_interval_sec: Annotated[float, Query(ge=0.2, le=30.0)] = 2.0,
) -> None:
    if not _websocket_allows_viewer(websocket, token):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    try:
        while True:
            if hasattr(session, "expire_all"):
                session.expire_all()
            snapshot = p5.run_jobs_snapshot(session, run_id)
            if snapshot is None:
                await websocket.send_json({"type": "error", "detail": "Run not found"})
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return

            payload = RunSnapshotPayload.model_validate({"type": "run_snapshot", **snapshot})
            await websocket.send_json(jsonable_encoder(payload))
            if once:
                await websocket.close()
                return
            await asyncio.sleep(poll_interval_sec)
    except WebSocketDisconnect:
        return


@router.post(
    "/runs/{run_id}/cancel",
    response_model=RunRead,
    dependencies=[Depends(require_operator)],
    tags=["runs"],
)
def cancel_run(run_id: uuid.UUID, session: SessionDep) -> RunRead:
    run = session.get(MaintenanceRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    run.status = "cancelled"
    run.finished_at = datetime.now(UTC)
    jobs = session.scalars(select(MaintenanceJob).where(MaintenanceJob.run_id == run_id))
    for job in jobs:
        cancellable = {
            JobStatus.QUEUED.value,
            JobStatus.ADMITTED.value,
            JobStatus.RUNNING.value,
        }
        if job.status in cancellable:
            job.status = JobStatus.SKIPPED.value
            job.error = "cancelled"
            job.finished_at = run.finished_at
    session.commit()
    session.refresh(run)
    return RunRead.model_validate(p4.run_summary(run))


@router.get(
    "/jobs/{job_id}",
    response_model=JobRead,
    dependencies=[Depends(require_viewer)],
    tags=["runs"],
)
def get_job(job_id: uuid.UUID, session: SessionDep) -> JobRead:
    job = session.get(MaintenanceJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobRead.model_validate(p4.job_summary(job))


def _websocket_allows_viewer(websocket: WebSocket, token: str | None) -> bool:
    auth_header = websocket.headers.get("authorization")
    token_value = token
    if auth_header:
        scheme, _, credentials = auth_header.partition(" ")
        if scheme.lower() == "bearer" and credentials:
            token_value = credentials

    if not token_value:
        return False
    principal = principal_from_token(token_value)
    return principal is not None and principal.can(Role.VIEWER)


def _parse_filter(raw: str | None) -> ColumnElement[bool] | None:
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
        return compile_predicate(parsed)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _validate_filter(payload: dict[str, object]) -> None:
    try:
        compile_predicate(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _resolve_selection(session: Session, selection: AssignmentSelection) -> p4.SelectionResult:
    try:
        return p4.resolve_selection(
            session,
            mode=selection.mode,
            ids=selection.ids,
            filter_payload=selection.filter,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _sort_clauses(sort: str | None) -> tuple[Any, ...]:
    if not sort:
        return (Table.schema_name, Table.table_name)
    clauses: list[ColumnElement[object]] = []
    for raw_field in sort.split(","):
        field = raw_field.strip()
        if not field:
            continue
        descending = field.startswith("-")
        field_name = field[1:] if descending else field
        column = SORT_FIELDS.get(field_name)
        if column is None:
            raise HTTPException(status_code=400, detail=f"Unsupported sort field: {field_name}")
        if descending:
            clauses.append(column.desc().nulls_last())
        else:
            clauses.append(column.asc().nulls_last())
    return tuple(clauses or [Table.schema_name, Table.table_name])


def _apply_patch(model: object, values: dict[str, object]) -> None:
    for key, value in values.items():
        setattr(model, key, value)


def _renderer_for(
    engine: EngineType,
) -> Callable[[MaintenancePlan], list[RenderedStatement]]:
    if engine is EngineType.TRINO:
        return render_trino
    if engine is EngineType.SPARK_EMR:
        return render_spark
    raise HTTPException(status_code=400, detail=f"Unsupported engine: {engine}")
