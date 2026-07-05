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

from cryo.api.deps import CurrentPrincipal, SessionDep, require_admin, require_operator, require_viewer
from cryo.api.predicates import compile_predicate
from cryo.api.preview_cost import (
    savings_projection_for_plan,
    savings_projection_to_dict,
)
from cryo.api.schemas import (
    AssignmentDeleteRequest,
    AssignmentRequest,
    AssignmentResponse,
    AssignmentSelection,
    AWSConnectionTestResponse,
    AWSIntegrationCreate,
    AWSIntegrationPatch,
    AWSIntegrationRead,
    AWSUsageResponse,
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
    SavingsProjectionRead,
    ScannerConfigRead,
    ScannerConfigUpdate,
    ScannerStatusRead,
    ScanTriggerRequest,
    ScanTriggerResponse,
    SchemaCostDashboardResponse,
    SourceCreate,
    SourcePatch,
    SourceRead,
    SourceTestRequest,
    SourceTestResponse,
    TableAuditResponse,
    TableCostRead,
    TablePartitionsResponse,
    TablePreviewRead,
    TableRead,
    TableStatsHistoryResponse,
)
from cryo.api.security import Role, principal_from_token
from cryo.api.serializers import table_to_read
from cryo.api.validators import validate_cron_field
from cryo.config import Settings, get_settings
from cryo.db.models import (
    AWSIntegration,
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
from cryo.db.repositories import datalake, presets
from cryo.db.repositories import scanner as scanner_repo
from cryo.db.repositories.run_finalizer import finalize_open_runs
from cryo.db.session import SessionLocal
from cryo.db.tenant_scope import (
    get_scoped_resource,
    require_tenant,
    scope_by_tenant,
    scoped_select,
    tenant_id_for_create,
)
from cryo.domain.enums import EngineType, JobStatus, Mode
from cryo.domain.models import MaintenancePlan
from cryo.engines.base import RenderedStatement
from cryo.engines.spark.sql import render as render_spark
from cryo.engines.trino.engine import TrinoEngine
from cryo.engines.trino.sql import render as render_trino
from cryo.infra.wiring import build_runtime_wiring
from cryo.integrations.aws_credentials import credentials_from_integration
from cryo.integrations.aws_s3_metrics import (
    fetch_bucket_storage_metrics,
    fetch_s3_price_per_gb,
)
from cryo.integrations.aws_s3_metrics import test_aws_connection as check_aws_connection
from cryo.planner.guardrails import validate_operation_order
from cryo.planner.planner import build_plan
from cryo.security.crypto import encrypt_secret
from cryo.sources.factory import catalog_from_config

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
    "estimated_monthly_s3_cost_usd": TableStats.estimated_monthly_s3_cost_usd,
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
    return datalake.dashboard_datalake(session, top_limit=top_limit, drift_limit=drift_limit)


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
    return datalake.dashboard_schema_cost(session, limit=limit)


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
    return datalake.dashboard_cost(
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
    payload = datalake.table_cost(session, table_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Table cost not found")
    return payload


@router.get(
    "/sources",
    response_model=list[SourceRead],
    dependencies=[Depends(require_viewer)],
)
def list_sources(session: SessionDep) -> list[Source]:
    return list(session.scalars(scoped_select(session, Source, Source.name)))


@router.get(
    "/sources/{source_id}",
    response_model=SourceRead,
    dependencies=[Depends(require_viewer)],
)
def get_source(source_id: uuid.UUID, session: SessionDep) -> Source:
    return get_scoped_resource(session, Source, source_id, resource_type="Source")


@router.post(
    "/sources",
    response_model=SourceRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
def create_source(payload: SourceCreate, session: SessionDep) -> Source:
    data = payload.model_dump()
    tenant_id = tenant_id_for_create(session)
    if tenant_id is not None:
        data["tenant_id"] = tenant_id
    if data.get("secret_ref"):
        data["secret_ref"] = encrypt_secret(data["secret_ref"])
    source = Source(**data)
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


@router.post(
    "/sources/test",
    response_model=SourceTestResponse,
    dependencies=[Depends(require_operator)],
)
def test_source(payload: SourceTestRequest) -> SourceTestResponse:
    """Verify a not-yet-saved catalog config and show what's actually in it.

    Used by the "Test connection" action while creating/editing a lakehouse, so a user can see
    real discovered namespaces and tables before committing to a Warehouse/URI guess.
    """
    try:
        catalog = catalog_from_config("__test__", payload.type, payload.config)
        tables = catalog.list_tables()
    except Exception as exc:
        return SourceTestResponse(ok=False, error=str(exc))

    namespaces = sorted({schema for schema, _ in tables})
    sample = [f"{schema}.{table}" for schema, table in tables[:10]]
    return SourceTestResponse(
        ok=True,
        namespaces=namespaces,
        table_count=len(tables),
        sample_tables=sample,
    )


@router.patch(
    "/sources/{source_id}",
    response_model=SourceRead,
    dependencies=[Depends(require_admin)],
)
def patch_source(source_id: uuid.UUID, payload: SourcePatch, session: SessionDep) -> Source:
    source = get_scoped_resource(session, Source, source_id, resource_type="Source")
    values = payload.model_dump(exclude_unset=True)
    if "secret_ref" in values and values["secret_ref"]:
        values["secret_ref"] = encrypt_secret(values["secret_ref"])
    _apply_patch(source, values)
    session.commit()
    session.refresh(source)
    return source


@router.delete(
    "/sources/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
def delete_source(source_id: uuid.UUID, session: SessionDep) -> None:
    source = get_scoped_resource(session, Source, source_id, resource_type="Source")
    source.enabled = False
    session.commit()


@router.get(
    "/sources/{source_id}/aws-integration",
    response_model=AWSIntegrationRead,
    dependencies=[Depends(require_viewer)],
    tags=["sources"],
)
def get_aws_integration(source_id: uuid.UUID, session: SessionDep) -> AWSIntegration:
    integration = session.scalar(
        select(AWSIntegration).where(AWSIntegration.source_id == source_id)
    )
    if integration is None:
        raise HTTPException(status_code=404, detail="No AWS integration configured")
    return integration


@router.post(
    "/sources/{source_id}/aws-integration",
    response_model=AWSIntegrationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
    tags=["sources"],
)
def create_aws_integration(
    source_id: uuid.UUID, payload: AWSIntegrationCreate, session: SessionDep
) -> AWSIntegration:
    get_scoped_resource(session, Source, source_id, resource_type="Source")
    existing = session.scalar(select(AWSIntegration).where(AWSIntegration.source_id == source_id))
    if existing is not None:
        raise HTTPException(
            status_code=400, detail="AWS integration already exists for this source"
        )
    integration = AWSIntegration(
        source_id=source_id,
        bucket=payload.bucket,
        region=payload.region,
        access_key_id=payload.access_key_id,
        secret_ref=encrypt_secret(payload.secret_access_key),
        enabled=payload.enabled,
    )
    session.add(integration)
    session.commit()
    session.refresh(integration)
    return integration


@router.patch(
    "/sources/{source_id}/aws-integration",
    response_model=AWSIntegrationRead,
    dependencies=[Depends(require_admin)],
    tags=["sources"],
)
def patch_aws_integration(
    source_id: uuid.UUID, payload: AWSIntegrationPatch, session: SessionDep
) -> AWSIntegration:
    integration = session.scalar(
        select(AWSIntegration).where(AWSIntegration.source_id == source_id)
    )
    if integration is None:
        raise HTTPException(status_code=404, detail="No AWS integration configured")
    values = payload.model_dump(exclude_unset=True)
    if "secret_access_key" in values:
        values["secret_ref"] = encrypt_secret(values.pop("secret_access_key"))
    _apply_patch(integration, values)
    session.commit()
    session.refresh(integration)
    return integration


@router.delete(
    "/sources/{source_id}/aws-integration",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
    tags=["sources"],
)
def delete_aws_integration(source_id: uuid.UUID, session: SessionDep) -> None:
    integration = session.scalar(
        select(AWSIntegration).where(AWSIntegration.source_id == source_id)
    )
    if integration is None:
        raise HTTPException(status_code=404, detail="No AWS integration configured")
    session.delete(integration)
    session.commit()


@router.post(
    "/sources/{source_id}/aws-integration/test",
    response_model=AWSConnectionTestResponse,
    dependencies=[Depends(require_operator)],
    tags=["sources"],
)
def test_aws_integration(source_id: uuid.UUID, session: SessionDep) -> AWSConnectionTestResponse:
    """Verify AWS credentials work via sts:GetCallerIdentity — free, no AWS charges."""
    integration = session.scalar(
        select(AWSIntegration).where(AWSIntegration.source_id == source_id)
    )
    if integration is None:
        raise HTTPException(status_code=404, detail="No AWS integration configured")
    result = check_aws_connection(credentials_from_integration(integration))
    integration.last_tested_at = datetime.now(UTC)
    integration.last_test_ok = result.ok
    session.commit()
    return AWSConnectionTestResponse(
        ok=result.ok, account_id=result.account_id, arn=result.arn, error=result.error
    )


@router.get(
    "/sources/{source_id}/aws-usage",
    response_model=AWSUsageResponse,
    dependencies=[Depends(require_viewer)],
    tags=["sources"],
)
def get_aws_usage(source_id: uuid.UUID, session: SessionDep) -> AWSUsageResponse:
    """Real S3 bucket size/object count (free daily CloudWatch metrics) and the real regional
    $/GB-month rate (free Price List API) — no data scan, no AWS charges."""
    integration = session.scalar(
        select(AWSIntegration).where(AWSIntegration.source_id == source_id)
    )
    if integration is None or not integration.enabled:
        raise HTTPException(status_code=404, detail="No enabled AWS integration configured")
    credentials = credentials_from_integration(integration)
    metrics = fetch_bucket_storage_metrics(integration.bucket, credentials)
    price = fetch_s3_price_per_gb(integration.region, credentials)
    return AWSUsageResponse(
        bucket=metrics.bucket,
        region=metrics.region,
        measured_at=metrics.measured_at,
        size_bytes_by_storage_class=dict(metrics.size_bytes_by_storage_class),
        total_size_bytes=metrics.total_size_bytes,
        object_count=metrics.object_count,
        price_per_gb_month=price.price_per_gb_month if price else None,
        price_source=price.source if price else None,
    )


@router.get(
    "/destinations",
    response_model=list[DestinationRead],
    dependencies=[Depends(require_viewer)],
)
def list_destinations(session: SessionDep) -> list[Destination]:
    return list(session.scalars(scoped_select(session, Destination, Destination.name)))


@router.get(
    "/destinations/{destination_id}",
    response_model=DestinationRead,
    dependencies=[Depends(require_viewer)],
)
def get_destination(destination_id: uuid.UUID, session: SessionDep) -> Destination:
    return get_scoped_resource(session, Destination, destination_id, resource_type="Destination")


@router.post(
    "/destinations",
    response_model=DestinationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
def create_destination(payload: DestinationCreate, session: SessionDep) -> Destination:
    data = payload.model_dump(mode="json")
    tenant_id = tenant_id_for_create(session)
    if tenant_id is not None:
        data["tenant_id"] = tenant_id
    if data.get("secret_ref"):
        data["secret_ref"] = encrypt_secret(data["secret_ref"])
    destination = Destination(**data)
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
    destination = get_scoped_resource(
        session, Destination, destination_id, resource_type="Destination"
    )
    values = payload.model_dump(exclude_unset=True, mode="json")
    if values.get("secret_ref"):
        values["secret_ref"] = encrypt_secret(values["secret_ref"])
    _apply_patch(destination, values)
    session.commit()
    session.refresh(destination)
    return destination


@router.delete(
    "/destinations/{destination_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
def delete_destination(destination_id: uuid.UUID, session: SessionDep) -> None:
    destination = get_scoped_resource(
        session, Destination, destination_id, resource_type="Destination"
    )
    destination.enabled = False
    session.commit()


@router.post(
    "/destinations/{destination_id}/test",
    dependencies=[Depends(require_operator)],
)
def test_destination(destination_id: uuid.UUID, session: SessionDep) -> dict[str, object]:
    destination = get_scoped_resource(
        session, Destination, destination_id, resource_type="Destination"
    )
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
    return list(session.scalars(scoped_select(session, Preset, Preset.name)))


@router.get(
    "/presets/{preset_id}",
    response_model=PresetRead,
    dependencies=[Depends(require_viewer)],
)
def get_preset(preset_id: uuid.UUID, session: SessionDep) -> Preset:
    return get_scoped_resource(session, Preset, preset_id, resource_type="Preset")


@router.post(
    "/presets",
    response_model=PresetRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
def create_preset(payload: PresetCreate, session: SessionDep) -> Preset:
    get_scoped_resource(session, Destination, payload.destination_id, resource_type="Destination")
    values = payload.model_dump()
    values["mode"] = payload.mode.value
    tenant_id = tenant_id_for_create(session)
    if tenant_id is not None:
        values["tenant_id"] = tenant_id
    try:
        values["schedule_cron"] = validate_cron_field(values.get("schedule_cron"), required=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    preset = get_scoped_resource(session, Preset, preset_id, resource_type="Preset")
    values = payload.model_dump(exclude_unset=True)
    if payload.mode is not None:
        values["mode"] = payload.mode.value
    if "schedule_cron" in values:
        try:
            values["schedule_cron"] = validate_cron_field(
                values.get("schedule_cron"),
                required=False,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    destination_id = values.get("destination_id")
    if destination_id is not None:
        get_scoped_resource(session, Destination, destination_id, resource_type="Destination")
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
    sample, estimate = presets.simulate_preset(session, preset)
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
    tenant_id = require_tenant(session)
    stmt = (
        select(Table)
        .outerjoin(TableStats)
        .outerjoin(TableCost)
        .options(selectinload(Table.stats), selectinload(Table.cost))
    )
    count_stmt = select(func.count()).select_from(Table).outerjoin(TableStats).outerjoin(TableCost)
    stmt = scope_by_tenant(stmt, Table, tenant_id)
    count_stmt = scope_by_tenant(count_stmt, Table, tenant_id)
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
    get_scoped_resource(session, Table, table_id, resource_type="Table")
    table = session.scalar(
        select(Table)
        .where(Table.id == table_id)
        .options(
            selectinload(Table.stats),
            selectinload(Table.partitions),
            selectinload(Table.cost),
        )
    )
    assert table is not None
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
    result = datalake.table_partitions(
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
    result = datalake.table_audit(session, table_id, limit=limit, offset=offset)
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
    result = datalake.table_history(session, table_id, limit=limit)
    if result is None:
        raise HTTPException(status_code=404, detail="Table not found")
    return result


@router.get(
    "/tables/{table_id}/effective-preset",
    response_model=EffectivePresetResponse,
    dependencies=[Depends(require_viewer)],
)
def get_effective_preset(table_id: uuid.UUID, session: SessionDep) -> EffectivePresetResponse:
    table = presets.get_table_with_stats(session, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")
    effective = presets.resolve_effective_preset(session, table)
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
    affected = presets.assign_tables(
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
    affected = presets.unassign_tables(session, table_ids=selection.table_ids)
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
    settings = get_settings()
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
                payload=dict(statement.payload),
            )
            for statement in renderer(plan)
        ]
        op_types = [op.type for op in plan.operations]
        projection = savings_projection_for_plan(
            stats, settings, params=payload.params, operations=op_types
        )
        savings_payload = SavingsProjectionRead(**savings_projection_to_dict(projection))
        items.append(
            TablePreviewRead(
                table_ref=plan.table_ref,
                notes=list(plan.notes),
                statements=statements,
                savings=savings_payload,
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
    payload: MaintenanceRunRequest, session: SessionDep, principal: CurrentPrincipal
) -> MaintenanceRunResponse:
    if session.get(Destination, payload.destination_id) is None:
        raise HTTPException(status_code=400, detail="Destination does not exist")
    preset = session.get(Preset, payload.preset_id) if payload.preset_id else None
    if payload.preset_id and preset is None:
        raise HTTPException(status_code=400, detail="Preset does not exist")

    requested_ops: list[str] = []
    if preset is not None:
        for op in preset.operations or []:
            if isinstance(op, str):
                requested_ops.append(op)
            elif isinstance(op, dict) and "type" in op:
                requested_ops.append(str(op["type"]))
    if requested_ops:
        warnings = validate_operation_order(requested_ops)
        if warnings:
            raise HTTPException(status_code=400, detail={"warnings": warnings, "blocking": True})

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
        summary={"actor": principal.subject},
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
    wiring = build_runtime_wiring()
    for job in jobs:
        wiring.queue.enqueue(str(job.id), str(job.destination_id))
        session.refresh(job)
    return MaintenanceRunResponse(
        run_id=run.id, job_ids=[job.id for job in jobs], status=run.status
    )


@router.post(
    "/maintenance/manifest-rewrite",
    response_model=MaintenanceRunResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_operator)],
)
def create_manifest_rewrite_run(
    payload: MaintenanceRunRequest, session: SessionDep, principal: CurrentPrincipal
) -> MaintenanceRunResponse:
    payload = payload.model_copy(update={"mode": Mode.MANIFEST_ONLY})
    return create_maintenance_run(payload, session, principal)


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
    if payload.selection is not None:
        selection = _resolve_selection(session, payload.selection)
        payload = payload.model_copy(
            update={"scope": "tables", "table_ids": selection.table_ids, "selection": None}
        )
    try:
        result = datalake.create_scan_trigger(session, payload.model_dump(mode="python"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    config = scanner_repo.get_scanner_config(session)
    num_chunks = payload.num_chunks or (
        config.num_chunks if payload.scope in {"all", "source", "schemas"} else 1
    )
    try:
        from cryo.infra.wiring import build_runtime_wiring
        from cryo.queue.scan_queue import RedisScanQueue
        from cryo.scanner.control import enqueue_scan_run

        wiring = build_runtime_wiring()
        scan_queue = RedisScanQueue(wiring.redis_client)
        raw_table_ids = result.get("table_ids", [])
        raw_schema_names = result.get("schema_names", [])
        table_ids = raw_table_ids if isinstance(raw_table_ids, list) else []
        schema_names = raw_schema_names if isinstance(raw_schema_names, list) else []
        enqueue_result = enqueue_scan_run(
            session,
            scan_queue,
            run_id=uuid.UUID(str(result["run_id"])),
            num_chunks=num_chunks,
            payload={
                "scope": result["scope"],
                "source_id": str(result["source_id"]) if result.get("source_id") else None,
                "table_ids": [str(table_id) for table_id in table_ids],
                "schema_names": [str(schema_name) for schema_name in schema_names],
                "cost_full_scan": bool(payload.cost_full_scan),
                "full_scan": bool(payload.full_scan),
                "trigger": payload.trigger,
            },
        )
        result = {
            **result,
            "queued": enqueue_result.queued,
            "message": enqueue_result.message,
            "num_chunks": enqueue_result.num_chunks,
        }
    except Exception as exc:
        if hasattr(session, "get"):
            run = session.get(MaintenanceRun, result["run_id"])
            if run is not None:
                run.summary = {
                    **dict(run.summary or {}),
                    "queued": False,
                    "queue_error": str(exc),
                    "placeholder": False,
                }
                session.commit()
        result = {
            **result,
            "queued": False,
            "message": f"Scan request accepted, but queueing failed: {exc}",
            "num_chunks": num_chunks,
        }
    return result


@router.get(
    "/scanner/config",
    response_model=ScannerConfigRead,
    dependencies=[Depends(require_viewer)],
    tags=["scans"],
)
def get_scanner_config(session: SessionDep) -> ScannerConfigRead:
    return ScannerConfigRead.model_validate(scanner_repo.get_scanner_config(session))


@router.patch(
    "/scanner/config",
    response_model=ScannerConfigRead,
    dependencies=[Depends(require_operator)],
    tags=["scans"],
)
def patch_scanner_config(
    payload: ScannerConfigUpdate,
    session: SessionDep,
) -> ScannerConfigRead:
    try:
        values = payload.model_dump(exclude_unset=True)
        if "schedule_cron" in values:
            values["schedule_cron"] = validate_cron_field(
                values.get("schedule_cron"),
                required=True,
            )
        config = scanner_repo.update_scanner_config(session, values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ScannerConfigRead.model_validate(config)


@router.get(
    "/scanner/status",
    response_model=ScannerStatusRead,
    dependencies=[Depends(require_viewer)],
    tags=["scans"],
)
def get_scanner_status(session: SessionDep) -> ScannerStatusRead:
    queue_depth: int | None = None
    try:
        from cryo.infra.wiring import build_runtime_wiring
        from cryo.queue.scan_queue import RedisScanQueue

        wiring = build_runtime_wiring()
        queue_depth = RedisScanQueue(wiring.redis_client).pending_count()
    except Exception:
        queue_depth = None

    status_payload = scanner_repo.scanner_status(session, queue_depth=queue_depth)
    return ScannerStatusRead.model_validate(
        {
            **status_payload,
            "config": ScannerConfigRead.model_validate(status_payload["config"]),
            "latest_run": _run_read_or_none(status_payload.get("latest_run")),
            "active_run": _run_read_or_none(status_payload.get("active_run")),
            "last_successful_run": _run_read_or_none(status_payload.get("last_successful_run")),
        }
    )


@router.get(
    "/runs",
    response_model=list[RunRead],
    dependencies=[Depends(require_viewer)],
    tags=["runs"],
)
def list_runs(
    session: SessionDep,
    kind: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[RunRead]:
    finalize_open_runs(SessionLocal)
    stmt = (
        select(MaintenanceRun)
        .options(selectinload(MaintenanceRun.jobs).selectinload(MaintenanceJob.table))
        .order_by(MaintenanceRun.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if kind:
        stmt = stmt.where(MaintenanceRun.kind == kind)
    runs = session.scalars(stmt)
    return [RunRead.model_validate(presets.run_summary(run)) for run in runs]


@router.get(
    "/runs/{run_id}",
    response_model=RunRead,
    dependencies=[Depends(require_viewer)],
    tags=["runs"],
)
def get_run(run_id: uuid.UUID, session: SessionDep) -> RunRead:
    finalize_open_runs(SessionLocal)
    run = session.scalar(
        select(MaintenanceRun)
        .where(MaintenanceRun.id == run_id)
        .options(selectinload(MaintenanceRun.jobs).selectinload(MaintenanceJob.table))
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunRead.model_validate(presets.run_summary(run))


@router.get(
    "/runs/{run_id}/jobs",
    response_model=RunJobsResponse,
    dependencies=[Depends(require_viewer)],
    tags=["runs"],
)
def get_run_jobs(run_id: uuid.UUID, session: SessionDep) -> dict[str, object]:
    finalize_open_runs(SessionLocal)
    snapshot = datalake.run_jobs_snapshot(session, run_id)
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
            snapshot = datalake.run_jobs_snapshot(session, run_id)
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
    if run.kind == "scan":
        scanner_repo.request_scan_cancel(session, run)
        session.commit()
        session.refresh(run)
        return RunRead.model_validate(presets.run_summary(run))
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
    return RunRead.model_validate(presets.run_summary(run))


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
    return JobRead.model_validate(presets.job_summary(job))


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


def _run_read_or_none(run: object | None) -> RunRead | None:
    if not isinstance(run, MaintenanceRun):
        return None
    return RunRead.model_validate(presets.run_summary(run))


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


def _resolve_selection(session: Session, selection: AssignmentSelection) -> presets.SelectionResult:
    try:
        return presets.resolve_selection(
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
