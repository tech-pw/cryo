from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.encoders import jsonable_encoder
from sqlalchemy import case, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql.elements import ColumnElement

from cryo.api.deps import (
    CurrentPrincipal,
    SessionDep,
    require_admin,
    require_editor,
    require_operator,
    require_viewer,
)
from cryo.api.predicates import compile_predicate
from cryo.api.preview_cost import (
    savings_projection_for_plan,
    savings_projection_to_dict,
)
from cryo.api.redact import unredact_merge
from cryo.api.route_helpers import (
    CONNECTION_TEST_TIMEOUT_SECONDS,
    NAMESPACE_LIST_TIMEOUT_SECONDS,
)
from cryo.api.route_helpers import (
    run_with_timeout as _run_with_timeout,
)
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
    DestinationTestRequest,
    DestinationTestResponse,
    EffectiveParamsResponse,
    EffectivePresetResponse,
    JobRead,
    MaintenancePreviewRequest,
    MaintenancePreviewResponse,
    MaintenanceRunRequest,
    MaintenanceRunResponse,
    OrphanScanCostRead,
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
    SourceNamespacesResponse,
    SourceNamespaceTablesResponse,
    SourcePatch,
    SourceRead,
    SourceTestRequest,
    SourceTestResponse,
    TableAlertSubscriptionCreate,
    TableAlertSubscriptionRead,
    TableAuditResponse,
    TableCostRead,
    TableDeactivateBatchRequest,
    TableDeactivateBatchResponse,
    TableGroupByResponse,
    TableGroupByRow,
    TableParamOverridePatch,
    TablePartitionsResponse,
    TablePatch,
    TablePreviewRead,
    TableRead,
    TableRunsResponse,
    TableStatsHistoryResponse,
    validate_destination_config,
)
from cryo.api.security import Principal, Role, principal_from_token
from cryo.api.serializers import table_to_read
from cryo.api.validators import validate_cron_field
from cryo.config import Settings, get_settings
from cryo.db.models import (
    Destination,
    MaintenanceJob,
    MaintenanceRun,
    NativeDispatch,
    Preset,
    PresetRule,
    QueryLog,
    Source,
    Table,
    TableAlertSubscription,
    TableAssignment,
    TableCost,
    TableStats,
)
from cryo.db.repositories import datalake, presets
from cryo.db.repositories import scanner as scanner_repo
from cryo.db.repositories.run_finalizer import finalize_open_runs
from cryo.db.repositories.sources import (
    deactivate_out_of_scope_tables,
    deactivate_tables_by_id,
    normalize_maintenance_schema_names,
)
from cryo.db.session import SessionLocal
from cryo.db.tenant_scope import (
    bind_tenant,
    get_scoped_resource,
    require_tenant,
    scope_by_tenant,
    scoped_select,
    tenant_id_for_create,
)
from cryo.domain.enums import EngineType, JobStatus, Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.base import RenderedStatement
from cryo.engines.native.engine import NativeEngine, is_native_available
from cryo.engines.native.render import render as render_native
from cryo.engines.spark.sql import render as render_spark
from cryo.engines.trino.engine import TrinoEngine
from cryo.engines.trino.sql import render as render_trino
from cryo.infra.wiring import build_runtime_wiring
from cryo.intents.binding import build_and_bind_job_intent
from cryo.observability.correlation import correlation_summary
from cryo.planner.guardrails import (
    sort_operations_canonically,
    validate_guardrails,
    validate_operation_order,
    validate_params,
)
from cryo.planner.planner import build_plan
from cryo.queue.run_events import build_job_status, build_log_line, safe_publish
from cryo.scanner.orphans import estimate_orphan_scan_cost
from cryo.security.crypto import encrypt_secret
from cryo.sources.factory import catalog_from_config, source_catalog_for

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)

SORT_FIELDS = {
    "schema_name": Table.schema_name,
    "table_name": Table.table_name,
    "consecutive_scan_failures": Table.consecutive_scan_failures,
    "last_scan_error_kind": Table.last_scan_error_kind,
    "last_snapshot_committed_at": TableStats.last_snapshot_committed_at,
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
    return datalake.dashboard_datalake(
        session, top_limit=top_limit, drift_limit=drift_limit, settings=Settings()
    )


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


@router.get(
    "/sources/{source_id}/namespaces",
    response_model=SourceNamespacesResponse,
    dependencies=[Depends(require_viewer)],
)
def get_source_namespaces(source_id: uuid.UUID, session: SessionDep) -> SourceNamespacesResponse:
    """Live namespace list for an already-saved source, so the scan-scope picker can offer real
    schema names to target even before this lakehouse has ever been scanned.

    Uses ``list_all_namespaces()`` (BFS, depth-bounded) instead of ``list_namespaces()`` so the
    picker shows nested namespaces too -- the Iceberg spec's top-level-only call hides every
    schema under an account/tenant prefix (e.g. ``prod_data.bronze.events``) and makes the picker
    effectively useless against REST catalogs.
    """
    source = get_scoped_resource(session, Source, source_id, resource_type="Source")

    def _connect_and_list_namespaces() -> list[str]:
        catalog = source_catalog_for(source)
        if hasattr(catalog, "list_all_namespaces"):
            return catalog.list_all_namespaces(max_depth=3)
        return catalog.list_namespaces()

    try:
        namespaces = _run_with_timeout(
            _connect_and_list_namespaces, seconds=_NAMESPACE_LIST_TIMEOUT_SECONDS
        )
    except Exception as exc:
        return SourceNamespacesResponse(ok=False, error=str(exc))
    return SourceNamespacesResponse(ok=True, namespaces=namespaces)


@router.get(
    "/sources/{source_id}/namespaces/{namespace}/tables",
    response_model=SourceNamespaceTablesResponse,
    dependencies=[Depends(require_viewer)],
)
def get_source_namespace_tables(
    source_id: uuid.UUID, namespace: str, session: SessionDep
) -> SourceNamespaceTablesResponse:
    """Live table names within one namespace of an already-saved source -- one cheap call, so
    the scan-scope "Specific tables" picker can offer real tables to target once a schema is
    chosen, even before this lakehouse has ever been scanned."""
    source = get_scoped_resource(session, Source, source_id, resource_type="Source")

    def _connect_and_list_tables() -> list[str]:
        catalog = source_catalog_for(source)
        return catalog.list_tables_in_namespace(namespace)

    try:
        tables = _run_with_timeout(_connect_and_list_tables)
    except Exception as exc:
        return SourceNamespaceTablesResponse(ok=False, error=str(exc))
    return SourceNamespaceTablesResponse(ok=True, tables=tables)


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
    data["maintenance_schema_names"] = normalize_maintenance_schema_names(
        data.get("maintenance_schema_names")
    )
    source = Source(**data)
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


_CONNECTION_TEST_TIMEOUT_SECONDS = CONNECTION_TEST_TIMEOUT_SECONDS
_NAMESPACE_LIST_TIMEOUT_SECONDS = NAMESPACE_LIST_TIMEOUT_SECONDS


@router.post(
    "/sources/test",
    response_model=SourceTestResponse,
    dependencies=[Depends(require_operator)],
)
def test_source(payload: SourceTestRequest, session: SessionDep) -> SourceTestResponse:
    """Verify a not-yet-saved catalog config connects -- deliberately does NOT enumerate tables.

    Used by the "Test connection" action while creating/editing a lakehouse. This only proves
    auth + reachability (one list_namespaces() call); it used to also call list_tables(), which
    is O(namespaces) round-trips and could take minutes against a real Hive Metastore with many
    databases -- exactly the kind of thing a human shouldn't be stuck waiting on a button click
    for. Real table/schema discovery is the async Scanner's job (cryo/scanner/run.py, triggered
    via POST /scan/trigger with scope="source"), which already has its own progress tracking.
    """
    config = payload.config
    if payload.source_id is not None:
        existing = get_scoped_resource(session, Source, payload.source_id, resource_type="Source")
        config = unredact_merge(config, dict(existing.config or {}))

    def _connect_and_list_namespaces() -> list[str]:
        catalog = catalog_from_config("__test__", payload.type, config)
        return catalog.list_namespaces()

    try:
        namespaces = _run_with_timeout(_connect_and_list_namespaces)
    except Exception as exc:
        return SourceTestResponse(ok=False, error=str(exc))

    return SourceTestResponse(
        ok=True,
        namespaces=namespaces,
        note=(
            "Connection verified. Table/schema discovery runs as a background scan after "
            "saving -- it isn't part of this quick check."
        ),
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
    if "config" in values and values["config"] is not None:
        # The client only ever sees a redacted config (see cryo/api/redact.py); submitting an
        # edit that didn't touch a secret field would otherwise persist the literal placeholder
        # string over the real value.
        values["config"] = unredact_merge(values["config"], dict(source.config or {}))
    if "maintenance_schema_names" in values:
        values["maintenance_schema_names"] = normalize_maintenance_schema_names(
            values["maintenance_schema_names"]
        )
    _apply_patch(source, values)
    if "maintenance_schema_names" in values:
        deactivate_out_of_scope_tables(
            session,
            source_id=source.id,
            maintenance_schema_names=source.maintenance_schema_names or [],
        )
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


@router.delete(
    "/sources/{source_id}/purge",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
def purge_source(source_id: uuid.UUID, session: SessionDep) -> None:
    """Permanently delete a source and everything Cryo scanned from it (tables, stats, tier
    policies, AWS integration). Unlike DELETE /sources/{id} above (which just disables it and is
    reversible via PATCH), this cannot be undone -- there is no soft-delete/restore path."""
    source = get_scoped_resource(session, Source, source_id, resource_type="Source")
    # QueryLog isn't covered by an ORM cascade relationship on Source (it's a raw ingestion log,
    # not scanned/managed state), so it needs explicit cleanup or the FK would block the delete.
    session.execute(delete(QueryLog).where(QueryLog.source_id == source_id))
    session.delete(source)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "Could not delete this lakehouse -- something still references it that this "
                "endpoint doesn't know how to clean up automatically."
            ),
        ) from exc


# AWS integration routes moved to cryo/api/routes_sources.py — see Move 22.
# Re-export for backward-compat with code that imports these from cryo.api.routes
# (the moved functions' behaviour and signatures are unchanged).
from cryo.api.routes_sources import (  # noqa: E402, F401
    create_aws_integration,
    delete_aws_integration,
    get_aws_integration,
    get_aws_usage,
    patch_aws_integration,
    test_aws_integration,
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
    try:
        _ensure_catalog_binding_sources(session, data.get("config") or {})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    if values.get("config") is not None:
        # See patch_source's comment: the client only ever sees a redacted config, so an edit
        # that didn't touch a secret field must not persist the literal placeholder over it.
        values["config"] = unredact_merge(values["config"], dict(destination.config or {}))
    effective_engine = values.get("engine", destination.engine)
    effective_config = values.get("config", destination.config or {})
    try:
        validate_destination_config(effective_engine, effective_config)
        _ensure_catalog_binding_sources(session, effective_config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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


@router.delete(
    "/destinations/{destination_id}/purge",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
def purge_destination(destination_id: uuid.UUID, session: SessionDep) -> None:
    """Permanently delete an engine. Unlike DELETE /destinations/{id} above (soft-disable,
    reversible via PATCH), this cannot be undone. Presets/maintenance runs that reference this
    engine are NOT cascade-deleted -- reassign or remove those first, or this fails with a clear
    409 rather than silently destroying preset configs or run history."""
    destination = get_scoped_resource(
        session, Destination, destination_id, resource_type="Destination"
    )
    session.delete(destination)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "Could not delete this engine -- one or more presets or maintenance runs still "
                "reference it. Reassign or delete those first."
            ),
        ) from exc


@router.post(
    "/destinations/test",
    response_model=DestinationTestResponse,
    dependencies=[Depends(require_operator)],
)
def test_destination_config(
    payload: DestinationTestRequest, session: SessionDep
) -> DestinationTestResponse:
    """Verify a not-yet-saved engine config, so a user can see it actually connects before
    committing to it. Mirrors /sources/test for lakehouses -- both let the create/edit drawer
    test with the form's current values instead of requiring a save first."""
    config = payload.config
    if payload.destination_id is not None:
        existing = get_scoped_resource(
            session, Destination, payload.destination_id, resource_type="Destination"
        )
        config = unredact_merge(config, dict(existing.config or {}))

    if payload.engine == EngineType.TRINO:
        try:
            snapshot = _run_with_timeout(TrinoEngine(dict(config)).probe_capacity)
            return DestinationTestResponse(
                ok=True,
                capacity=snapshot.capacity,
                used=snapshot.used,
                available=snapshot.available,
            )
        except Exception as exc:
            return DestinationTestResponse(ok=False, message=str(exc))
    if payload.engine == EngineType.NATIVE:
        dest_id = str(payload.destination_id) if payload.destination_id is not None else None
        return _test_native_destination(dict(config), destination_id=dest_id)
    return DestinationTestResponse(
        ok=True,
        message="Configuration present; live engine probe is implemented in the governor/worker.",
    )


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
            snapshot = _run_with_timeout(TrinoEngine(dict(destination.config)).probe_capacity)
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
    if destination.engine == EngineType.NATIVE.value:
        result = _test_native_destination(
            dict(destination.config or {}),
            destination_id=str(destination_id),
        )
        return {
            "ok": result.ok and destination.enabled,
            "engine": destination.engine,
            "name": destination.name,
            "capacity": result.capacity,
            "used": result.used,
            "available": result.available,
            "message": result.message,
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
    guardrail_errors = validate_guardrails(values.get("guardrails") or {})
    if guardrail_errors:
        raise HTTPException(status_code=400, detail={"guardrails": guardrail_errors})
    param_errors = validate_params(values.get("params") or {})
    if param_errors:
        raise HTTPException(status_code=400, detail={"params": param_errors})
    if values.get("operations"):
        values["operations"] = sort_operations_canonically(values["operations"])
    if values.get("is_default"):
        presets.clear_other_default_presets(
            session,
            tenant_id=tenant_id,
        )
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
    if "guardrails" in values:
        guardrail_errors = validate_guardrails(values.get("guardrails") or {})
        if guardrail_errors:
            raise HTTPException(status_code=400, detail={"guardrails": guardrail_errors})
    if "params" in values:
        param_errors = validate_params(values.get("params") or {})
        if param_errors:
            raise HTTPException(status_code=400, detail={"params": param_errors})
    if values.get("operations"):
        values["operations"] = sort_operations_canonically(values["operations"])
    if values.get("is_default"):
        presets.clear_other_default_presets(
            session,
            tenant_id=getattr(preset, "tenant_id", None),
            keep_preset_id=preset.id,
        )
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
    cursor: Annotated[str | None, Query()] = None,
) -> PaginatedTables:
    predicate = _parse_filter(filter)
    tenant_id = require_tenant(session)
    stmt = (
        select(Table)
        .outerjoin(TableStats)
        .outerjoin(TableCost)
        .outerjoin(TableAssignment)
        .options(
            selectinload(Table.source),
            selectinload(Table.stats),
            selectinload(Table.cost),
        )
    )
    count_stmt = (
        select(func.count())
        .select_from(Table)
        .outerjoin(TableStats)
        .outerjoin(TableCost)
        .outerjoin(TableAssignment)
    )
    stmt = scope_by_tenant(stmt, Table, tenant_id)
    count_stmt = scope_by_tenant(count_stmt, Table, tenant_id)
    if predicate is not None:
        stmt = stmt.where(predicate)
        count_stmt = count_stmt.where(predicate)

    total = session.scalar(count_stmt) or 0

    # Server-side cursor takes precedence over offset — clients pass back the
    # previous page's cursor instead of computing `offset += limit`. The two
    # compose: cursor pins the start of the page, limit caps its size.
    actual_offset = offset
    if cursor:
        from cryo.api.pagination import PageCursor, make_filter_repr, make_sort_repr

        try:
            decoded = PageCursor.decode(cursor)
        except Exception as exc:  # noqa: BLE001 - any decode problem ⇒ 400 with the message
            raise HTTPException(status_code=400, detail=f"Invalid cursor: {exc}") from exc
        if not decoded.matches_request(
            filter_repr=make_filter_repr(filter), sort_repr=make_sort_repr(sort)
        ):
            raise HTTPException(
                status_code=409,
                detail="Cursor was issued for a different filter/sort combination",
            )
        actual_offset = decoded.offset_within_key

    items = list(
        session.scalars(stmt.order_by(*_sort_clauses(sort)).offset(actual_offset).limit(limit))
    )

    # Emit a next-page cursor only when there might be more rows past this page.
    # The cheap "fetch limit+1 rows and trim" probe is correct without needing
    # a second count query — and on a fully-indexed sort the row read is O(1)
    # with the existing ORDER BY + LIMIT plan.
    next_cursor = None
    has_more = actual_offset + len(items) < total
    if has_more and items:
        from cryo.api.pagination import PageCursor, make_filter_repr, make_sort_repr

        next_cursor = PageCursor(
            key=str(items[-1].id),
            offset_within_key=actual_offset + len(items),
            filter_repr=make_filter_repr(filter),
            sort_repr=make_sort_repr(sort),
        ).encode()

    return PaginatedTables(
        items=[table_to_read(item) for item in items],
        total=total,
        limit=limit,
        offset=actual_offset,
        cursor=next_cursor,
        has_more=has_more,
    )


@router.get(
    "/tables/export.csv",
    response_class=Response,
    dependencies=[Depends(require_viewer)],
)
def export_tables_csv(
    session: SessionDep,
    filter: Annotated[str | None, Query()] = None,
    sort: Annotated[str | None, Query()] = None,
) -> Response:
    predicate = _parse_filter(filter)
    tenant_id = require_tenant(session)
    stmt = (
        select(
            Source.name,
            Table.schema_name,
            Table.table_name,
            Table.owner_email,
            Table.consecutive_scan_failures,
            Table.last_scan_error,
            Table.last_scan_error_at,
            TableStats.last_snapshot_committed_at,
            TableStats.scan_at,
            TableStats.grade,
            TableStats.overall_score,
            TableStats.total_size_gb,
            TableStats.small_files_count,
            TableStats.total_files,
            Table.last_scan_error_kind,
        )
        .select_from(Table)
        .join(Source, Source.id == Table.source_id)
        .outerjoin(TableStats)
        .outerjoin(TableCost)
        .outerjoin(TableAssignment)
    )
    stmt = scope_by_tenant(stmt, Table, tenant_id)
    if predicate is not None:
        stmt = stmt.where(predicate)
    stmt = stmt.order_by(*_sort_clauses(sort))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "source",
            "qualified_table",
            "schema",
            "table",
            "last_snapshot_committed_at",
            "last_scanned_at",
            "consecutive_scan_failures",
            "last_scan_error",
            "last_scan_error_at",
            "last_scan_error_kind",
            "grade",
            "health_score",
            "size_gb",
            "small_files",
            "total_files",
            "owner",
        ]
    )
    for row in session.execute(stmt):
        writer.writerow(
            [
                row[0],
                f"{row[1]}.{row[2]}",
                row[1],
                row[2],
                row[7].isoformat() if row[7] else "",
                row[8].isoformat() if row[8] else "",
                row[4],
                row[5] or "",
                row[6].isoformat() if row[6] else "",
                row[14] or "",
                row[9] or "",
                row[10] if row[10] is not None else "",
                row[11] if row[11] is not None else "",
                row[12] if row[12] is not None else "",
                row[13] if row[13] is not None else "",
                row[3] or "",
            ]
        )
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="cryo-tables.csv"'},
    )


_SIZE_BUCKET_EXPR = case(
    (TableStats.total_size_gb.is_(None), "Not scanned"),
    (TableStats.total_size_gb < 1, "< 1 GB"),
    (TableStats.total_size_gb < 10, "1-10 GB"),
    (TableStats.total_size_gb < 100, "10-100 GB"),
    (TableStats.total_size_gb < 1024, "100 GB - 1 TB"),
    else_="> 1 TB",
)

# Same predicate-friendly fields the /tables filter/sort already exposes, plus a computed
# size_bucket -- deliberately a small, curated set (not every FIELD_MAP column) since not every
# field makes sense to group by (e.g. grouping by total_files would produce ~one group per row).
# Untyped to match FIELD_MAP's own convention in cryo/api/predicates.py: mypy sees each
# InstrumentedAttribute's specific column type (str, bool, ...) rather than the common
# ColumnElement base, and typing this dict explicitly fights that rather than working with it.
_GROUP_BY_FIELDS = {
    "catalog": Source.name,
    "schema_name": Table.schema_name,
    "grade": TableStats.grade,
    "compaction_priority": TableStats.compaction_priority,
    "needs_compaction": TableStats.needs_compaction,
    "size_bucket": _SIZE_BUCKET_EXPR,
}


@router.get(
    "/tables/group-by",
    response_model=TableGroupByResponse,
    dependencies=[Depends(require_viewer)],
)
def group_tables(
    session: SessionDep,
    field: Annotated[str, Query()],
    filter: Annotated[str | None, Query()] = None,
) -> TableGroupByResponse:
    """Collective counts/totals per distinct value of ``field`` -- the Tables page's "group by"
    view. Reuses the same filter predicate as ``GET /tables`` so a group-by respects whatever
    the user has already filtered down to."""
    raw_group_col = _GROUP_BY_FIELDS.get(field)
    if raw_group_col is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported group-by field: {field!r}. Choose one of "
            f"{sorted(_GROUP_BY_FIELDS)}.",
        )
    group_col = cast("ColumnElement[Any]", raw_group_col)

    predicate = _parse_filter(filter)
    tenant_id = require_tenant(session)
    # "table_count", not "count" -- sqlalchemy.engine.Row inherits tuple.count(), and while Row
    # correctly prefers a labeled column over that method at runtime, mypy's stub resolves
    # row.count to the inherited bound method's type. Avoid the ambiguity outright.
    stmt = (
        select(
            group_col.label("group_value"),
            func.count(func.distinct(Table.id)).label("table_count"),
            func.coalesce(func.sum(TableStats.total_size_gb), 0).label("total_size_gb"),
            func.coalesce(func.sum(TableStats.small_files_count), 0).label("total_small_files"),
            func.coalesce(func.sum(TableStats.projected_savings_usd_month), 0).label(
                "total_savings_usd_month"
            ),
        )
        .select_from(Table)
        .outerjoin(TableStats)
        .outerjoin(TableCost)
        .outerjoin(TableAssignment)
    )
    if field == "catalog":
        stmt = stmt.join(Source, Source.id == Table.source_id)
    stmt = scope_by_tenant(stmt, Table, tenant_id)
    if predicate is not None:
        stmt = stmt.where(predicate)
    stmt = stmt.group_by(group_col).order_by(func.count(func.distinct(Table.id)).desc())

    rows = session.execute(stmt).all()
    return TableGroupByResponse(
        field=field,
        rows=[
            TableGroupByRow(
                group_value=str(row.group_value) if row.group_value is not None else None,
                count=row.table_count,
                total_size_gb=float(row.total_size_gb),
                total_small_files=int(row.total_small_files),
                total_savings_usd_month=float(row.total_savings_usd_month),
            )
            for row in rows
        ],
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
            selectinload(Table.source),
            selectinload(Table.stats),
            selectinload(Table.cost),
        )
    )
    assert table is not None
    return table_to_read(table)


@router.patch(
    "/tables/{table_id}",
    response_model=TableRead,
    dependencies=[Depends(require_editor)],
    tags=["tables"],
)
def patch_table(table_id: uuid.UUID, payload: TablePatch, session: SessionDep) -> TableRead:
    """Set table-level fields not owned by the scanner -- currently just the alert owner."""
    table = get_scoped_resource(session, Table, table_id, resource_type="Table")
    values = payload.model_dump(exclude_unset=True)
    for key, value in values.items():
        setattr(table, key, value)
    session.commit()
    session.refresh(table)
    return table_to_read(table)


@router.post(
    "/tables/deactivate-batch",
    response_model=TableDeactivateBatchResponse,
    dependencies=[Depends(require_admin)],
    tags=["tables"],
)
def deactivate_tables_batch(
    payload: TableDeactivateBatchRequest, session: SessionDep
) -> TableDeactivateBatchResponse:
    """Deactivate an explicit, caller-supplied list of table_ids (is_active=False only --
    never touches S3/underlying files, fully reversible via a direct DB update). Requires
    admin: unlike the automated deactivate_orphaned_evolving_catalog_entries sweep (which
    only matches a verified-safe location IS NULL shape), this trusts the caller's own
    per-table verification, so it's scoped to explicit IDs with no filter/heuristic that
    could silently expand scope. See deactivate_tables_by_id's docstring."""
    each_scoped = [
        get_scoped_resource(session, Table, table_id, resource_type="Table")
        for table_id in payload.table_ids
    ]
    deactivated = deactivate_tables_by_id(session, [table.id for table in each_scoped])
    session.commit()
    return TableDeactivateBatchResponse(
        requested=len(payload.table_ids),
        deactivated=deactivated,
        deactivated_ids=[table.id for table in each_scoped],
    )


@router.get(
    "/tables/{table_id}/alert-subscriptions",
    response_model=list[TableAlertSubscriptionRead],
    dependencies=[Depends(require_viewer)],
    tags=["tables"],
)
def list_table_alert_subscriptions(
    table_id: uuid.UUID, session: SessionDep
) -> list[TableAlertSubscription]:
    get_scoped_resource(session, Table, table_id, resource_type="Table")
    return list(
        session.scalars(
            select(TableAlertSubscription)
            .where(TableAlertSubscription.table_id == table_id)
            .order_by(TableAlertSubscription.created_at)
        )
    )


@router.post(
    "/tables/{table_id}/alert-subscriptions",
    response_model=TableAlertSubscriptionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_viewer)],
    tags=["tables"],
)
def create_table_alert_subscription(
    table_id: uuid.UUID,
    payload: TableAlertSubscriptionCreate,
    session: SessionDep,
) -> TableAlertSubscription:
    """Subscribe an email to SLA/data-quality breach alerts for this table.

    Any authenticated viewer can subscribe themselves (or a distro list) -- this is the
    "consumer wants to know" list, distinct from the single accountable owner set via
    PATCH /tables/{id}. Re-subscribing the same email is a no-op, not an error.
    """
    get_scoped_resource(session, Table, table_id, resource_type="Table")
    existing = session.scalar(
        select(TableAlertSubscription).where(
            TableAlertSubscription.table_id == table_id,
            TableAlertSubscription.email == payload.email,
        )
    )
    if existing is not None:
        return existing
    subscription = TableAlertSubscription(table_id=table_id, email=payload.email)
    session.add(subscription)
    session.commit()
    session.refresh(subscription)
    return subscription


@router.delete(
    "/tables/{table_id}/alert-subscriptions/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_viewer)],
    tags=["tables"],
)
def delete_table_alert_subscription(
    table_id: uuid.UUID, subscription_id: uuid.UUID, session: SessionDep
) -> None:
    get_scoped_resource(session, Table, table_id, resource_type="Table")
    subscription = session.get(TableAlertSubscription, subscription_id)
    if subscription is None or subscription.table_id != table_id:
        raise HTTPException(status_code=404, detail="Alert subscription not found")
    session.delete(subscription)
    session.commit()


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
    "/tables/{table_id}/runs",
    response_model=TableRunsResponse,
    dependencies=[Depends(require_viewer)],
    tags=["tables"],
)
def get_table_runs(
    table_id: uuid.UUID,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    result = datalake.table_runs(session, table_id, limit=limit, offset=offset)
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
    provenance = cast(
        Literal["assignment", "rule", "rule_after_stale_pin", "default", "none"],
        effective.provenance,
    )
    return EffectivePresetResponse(
        table_id=table_id,
        preset_id=uuid.UUID(effective.preset.id),
        preset_name=effective.preset.name,
        provenance=provenance,
        rule_id=uuid.UUID(effective.rule_id) if effective.rule_id else None,
    )


@router.get(
    "/tables/{table_id}/effective-params",
    response_model=EffectiveParamsResponse,
    dependencies=[Depends(require_viewer)],
)
def get_effective_params(table_id: uuid.UUID, session: SessionDep) -> EffectiveParamsResponse:
    table = presets.get_table_with_stats(session, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")
    effective = presets.resolve_effective_preset(session, table)
    assignment = session.get(TableAssignment, table_id)
    overrides = (
        dict(assignment.param_overrides) if assignment and assignment.param_overrides else {}
    )
    preset_params = dict(effective.preset.params) if effective else {}
    return EffectiveParamsResponse(
        table_id=table_id,
        preset_id=uuid.UUID(effective.preset.id) if effective else None,
        preset_name=effective.preset.name if effective else None,
        preset_params=preset_params,
        param_overrides=overrides,
        effective_params={**preset_params, **overrides},
    )


@router.patch(
    "/tables/{table_id}/param-overrides",
    response_model=EffectiveParamsResponse,
    dependencies=[Depends(require_admin)],
)
def patch_table_param_overrides(
    table_id: uuid.UUID, payload: TableParamOverridePatch, session: SessionDep
) -> EffectiveParamsResponse:
    """Per-table tweaks (e.g. a tighter retention_days for one hot/test table) layered on
    top of its assigned preset's params -- without forking the shared preset, which may be
    pinned to other tables that shouldn't be affected. Full-replace semantics: the given
    dict becomes the entire override set, same as PresetPatch.params.
    """
    table = get_scoped_resource(session, Table, table_id, resource_type="Table")
    param_errors = validate_params(payload.param_overrides)
    if param_errors:
        raise HTTPException(status_code=400, detail={"param_overrides": param_errors})
    assignment = session.get(TableAssignment, table_id)
    if assignment is None:
        effective = presets.resolve_effective_preset(session, table)
        if effective is None:
            raise HTTPException(
                status_code=400,
                detail="Table has no effective preset to override; assign one first",
            )
        assignment = TableAssignment(
            table_id=table_id,
            preset_id=uuid.UUID(effective.preset.id),
            pinned=True,
        )
        session.add(assignment)
    assignment.param_overrides = payload.param_overrides
    session.commit()
    return get_effective_params(table_id, session)


@router.get(
    "/tables/{table_id}/compatible-presets",
    response_model=list[PresetRead],
    dependencies=[Depends(require_viewer)],
)
def list_compatible_presets(table_id: uuid.UUID, session: SessionDep) -> list[Preset]:
    """Presets whose destination can maintain this table's catalog.

    Trino presets are included only when the table's source appears in the
    destination's ``catalog_bindings``. Non-Trino presets are always included.
    """
    table = get_scoped_resource(session, Table, table_id, resource_type="Table")
    return presets.list_compatible_presets(session, table)


@router.post(
    "/assignments",
    response_model=AssignmentResponse,
    dependencies=[Depends(require_operator)],
)
def assign_tables(payload: AssignmentRequest, session: SessionDep) -> AssignmentResponse:
    if session.get(Preset, payload.preset_id) is None:
        raise HTTPException(status_code=400, detail="Preset does not exist")

    selection = _resolve_selection(session, payload.selection)
    try:
        presets.assert_preset_compatible_with_tables(
            session,
            preset_id=payload.preset_id,
            table_ids=selection.table_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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


def _estimate_partition_depth(partitions: list[dict[str, Any]]) -> int:
    if not partitions:
        return 0
    return len(set().union(*(set(p.keys()) for p in partitions)))


def _parse_stats_as_of(stats: dict[str, Any]) -> datetime | None:
    raw = stats.get("scan_at") or stats.get("stats_as_of")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        parsed = raw
    else:
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


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
        stats_as_of = _parse_stats_as_of(table.stats)
        notes = list(plan.notes)
        if stats_as_of is not None:
            age_hours = (datetime.now(UTC) - stats_as_of).total_seconds() / 3600
            if age_hours > 24:
                notes.append(
                    f"Stats as of {stats_as_of.isoformat()} ({age_hours:.0f}h old) — "
                    "re-scan before execute if the table changed."
                )

        orphan_scan_cost_read: OrphanScanCostRead | None = None
        if OperationType.REMOVE_ORPHAN_FILES in op_types:
            total_files = stats.get("total_files", 0)
            partition_depth = _estimate_partition_depth(table.partitions)
            preflight = estimate_orphan_scan_cost(
                total_files=total_files,
                partition_depth=partition_depth,
                settings=settings,
            )
            orphan_scan_cost_read = OrphanScanCostRead(**asdict(preflight))

        items.append(
            TablePreviewRead(
                table_ref=plan.table_ref,
                notes=notes,
                statements=statements,
                savings=savings_payload,
                stats_as_of=stats_as_of,
                orphan_scan_cost=orphan_scan_cost_read,
            )
        )
    return MaintenancePreviewResponse(items=items)


def _adaptive_routing_destinations_for_run(
    session: Session, preset: Preset, table_ids: Sequence[uuid.UUID]
) -> dict[str, str]:
    """When `preset.guardrails.adaptive_routing` is on, re-resolve each table's destination
    the same way the scheduler's cron path does (`_apply_routing` in scheduler/service.py) --
    including consecutive-failure exclusion -- instead of forcing every job in this run onto
    the caller's single `payload.destination_id`.

    Regression, confirmed live 2026-08-11: `unified_bq.gold_dbt_pw_bq` failed on
    native-datafusion 4 times in a row. `consecutive_engine_failures()` correctly computed
    that streak and `default-native-deep`'s *cron*-triggered runs correctly routed around it --
    but every retry that actually hit this table went through this ad-hoc API path instead
    (`POST /maintenance/run`, trigger="api"/"claude-retry-failed-*"), which always used the
    single `destination_id` the caller passed and never consulted adaptive routing at all. The
    failure-exclusion logic was real and correct; it just never got a chance to run here.

    Returns `{table_id: destination_id}` only for tables whose destination should change;
    callers should fall back to `payload.destination_id` for any table_id not in the result
    (also what happens when adaptive_routing is off, or nothing routes anywhere else).
    """
    if not preset.guardrails.get("adaptive_routing"):
        return {}

    from cryo.db.repositories.presets import preset_to_config, table_to_candidate
    from cryo.scheduler.adaptive_routing import (
        AdaptiveRoutingContext,
        resolve_destination_adaptive,
    )
    from cryo.scheduler.routing import build_destinations_index

    tables = session.scalars(
        select(Table)
        .where(Table.id.in_(table_ids))
        .options(selectinload(Table.stats), selectinload(Table.cost))
    ).all()
    if not tables:
        return {}

    destinations = build_destinations_index(
        [
            {"id": str(d.id), "name": d.name, "engine": d.engine, "config": dict(d.config)}
            for d in session.scalars(select(Destination).where(Destination.enabled.is_(True)))
        ]
    )
    preset_config = preset_to_config(preset)

    # Bounded to exactly this run's tables, not the fleet -- list_tables() OOM-killed the API
    # pod once this session from an unbounded query; consecutive_engine_failures() is already
    # scoped this way for the same reason (see its own docstring).
    #
    # Regression, confirmed live 2026-08-11: this used to construct SQLSchedulerStore with
    # `lambda: session` standing in for its usual session_factory -- but SQLSchedulerStore's
    # methods do `with self._session_factory() as session:`, i.e. they call __exit__ on
    # whatever the "factory" returns. Passed the live request-scoped `session` object directly,
    # that closes/rolls back this route's own session partway through the request (right after
    # this helper returns), so the later MaintenanceRun/MaintenanceJob inserts in the same
    # request failed with "insert or update on table jobs violates foreign key constraint
    # fk_jobs_run_id_runs" -- the run row from earlier in the same (now-closed) transaction was
    # gone. Inlining the same query here, against the real request session with no extra
    # with-block around it, avoids ever needing a fake session factory.
    failure_history: dict[str, dict[str, int]] = {}
    table_ids_as_uuid = [table.id for table in tables]
    if table_ids_as_uuid:
        ranked = (
            select(
                MaintenanceJob.table_id,
                MaintenanceJob.destination_id,
                MaintenanceJob.status,
                func.row_number()
                .over(
                    partition_by=(MaintenanceJob.table_id, MaintenanceJob.destination_id),
                    order_by=MaintenanceJob.started_at.desc(),
                )
                .label("rn"),
            )
            .where(
                MaintenanceJob.table_id.in_(table_ids_as_uuid),
                MaintenanceJob.started_at.is_not(None),
                MaintenanceJob.status.in_(("succeeded", "failed", "partial")),
            )
            .subquery()
        )
        rows = session.execute(
            select(ranked.c.table_id, ranked.c.destination_id, ranked.c.status)
            .where(ranked.c.rn <= 10)
            .order_by(ranked.c.table_id, ranked.c.destination_id, ranked.c.rn)
        ).all()
        current_key: tuple[str, str] | None = None
        streak = 0
        broken = False
        for row_table_id, row_destination_id, row_status in rows:
            key = (str(row_table_id), str(row_destination_id))
            if key != current_key:
                current_key = key
                streak = 0
                broken = False
            if not broken:
                if row_status == "failed":
                    streak += 1
                else:
                    broken = True
            if streak:
                failure_history.setdefault(str(row_table_id), {})[str(row_destination_id)] = (
                    streak
                )

    routed: dict[str, str] = {}
    for table in tables:
        candidate = table_to_candidate(table)
        per_table_failures = failure_history.get(candidate.id)
        if per_table_failures:
            from dataclasses import replace

            candidate = replace(
                candidate,
                stats={**candidate.stats, "engine_consecutive_failures": per_table_failures},
            )
        route = resolve_destination_adaptive(
            candidate, preset_config, destinations, AdaptiveRoutingContext()
        )
        routed[candidate.id] = route.destination_id
    return routed


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
    preset = session.get(Preset, payload.preset_id)
    if preset is None:
        raise HTTPException(status_code=400, detail="Preset does not exist")

    requested_ops: list[str] = []
    if preset is not None:
        for op in preset.operations or []:
            if isinstance(op, str):
                requested_ops.append(op)
            elif isinstance(op, dict) and "type" in op:
                requested_ops.append(str(op["type"]))
    # Presets are stored canonically (sorted on write, plus a backfill migration), but a
    # hand-edited row must not brick the run: build_plan sorts by CORRECT_ORDER regardless,
    # so the executed order is canonical either way. Normalizing here keeps what we hand the
    # planner consistent with what actually runs, rather than rejecting the request.
    requested_ops = sort_operations_canonically(requested_ops)

    if payload.scheduled_at is not None:
        now = datetime.now(UTC)
        scheduled_at = payload.scheduled_at
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=UTC)
        if scheduled_at <= now:
            raise HTTPException(status_code=400, detail="scheduled_at must be in the future")

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
        status="scheduled" if payload.scheduled_at is not None else "queued",
        scheduled_at=payload.scheduled_at,
        summary=correlation_summary({"actor": principal.subject}),
    )
    session.add(run)
    session.flush()

    # Plan Verifier (arXiv:2510.09567) -- when the preset exposes a parsed MaintenancePlan via
    # params.operations (Planner-emitted), run the verifier once per table before enqueue and
    # persist the verdict onto the job. If the verifier reports a critical violation, refuse
    # to enqueue.
    settings = get_settings()
    verifier_enabled = settings.plan_verifier_enabled
    # Computed once for the whole run, not per-table -- avoids an N+1 query pattern and matches
    # how the scheduler's cron path (_apply_routing) resolves routing for a run as a batch.
    adaptive_destinations = _adaptive_routing_destinations_for_run(
        session, preset, selection.table_ids
    )
    jobs: list[MaintenanceJob] = []
    for table_id in selection.table_ids:
        if preset is not None and requested_ops:
            for op_name in requested_ops:
                from cryo.domain.enums import OperationType
                from cryo.security.abac_enforcement import evaluate_maintenance_allowed

                try:
                    op_enum = OperationType(op_name)
                except ValueError:
                    op_enum = op_name  # type: ignore[assignment]
                allowed, reason = evaluate_maintenance_allowed(
                    session, table_id, principal, op_enum
                )
                if not allowed:
                    raise HTTPException(status_code=403, detail=reason)
        plan_for_verifier = (
            None
            if settings.durable_intents_enabled
            else _build_plan_for_table(session, table_id, preset, payload.mode)
        )
        verified_flag = True
        plan_hash_value: str | None = None
        verifier_version_value: str | None = None
        violations_value: list[dict[str, object]] = []
        if verifier_enabled and plan_for_verifier is not None:
            from cryo.planner.verifier import PlanVerifier

            verdict = PlanVerifier(session=session, settings=get_settings()).verify(
                plan_for_verifier
            )
            verified_flag = verdict.verified
            plan_hash_value = verdict.plan_hash
            verifier_version_value = verdict.verifier_version
            violations_value = [
                {
                    "invariant": v.invariant,
                    "severity": v.severity,
                    "message": v.message,
                    "location": v.location,
                }
                for v in verdict.violations
            ]
            if not verdict.verified:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "plan_verifier_rejected",
                        "plan_hash": verdict.plan_hash,
                        "violations": violations_value,
                    },
                )
        routed_destination_id = adaptive_destinations.get(str(table_id))
        job = MaintenanceJob(
            run_id=run.id,
            table_id=table_id,
            destination_id=(
                uuid.UUID(routed_destination_id)
                if routed_destination_id is not None
                else payload.destination_id
            ),
            mode=payload.mode.value,
            status="queued",
            verified=verified_flag,
            plan_hash=plan_hash_value,
            verifier_version=verifier_version_value,
            verifier_violations=violations_value or None,
        )
        session.add(job)
        session.flush()
        if settings.durable_intents_enabled:
            build_and_bind_job_intent(
                session,
                job_id=str(job.id),
                settings=settings,
                actor_type="api",
                actor_id=principal.subject,
            )
        jobs.append(job)
    session.commit()
    wiring = build_runtime_wiring()
    if payload.scheduled_at is not None:
        # Scheduled runs are not enqueued now -- the jobs exist (status="queued" at the row
        # level, same as any other job) but wiring.queue.enqueue() is what actually makes a
        # job eligible for Governor admission, and that call is deliberately skipped here.
        # cryo/scheduler/service.py's dispatch tick enqueues these once scheduled_at passes.
        safe_publish(
            wiring.run_events,
            run.id,
            build_log_line(
                run_id=run.id,
                message=(
                    f"Scheduled maintenance run with {len(jobs)} job(s) for "
                    f"{payload.scheduled_at.isoformat()}"
                ),
            ),
        )
        return MaintenanceRunResponse(
            run_id=run.id, job_ids=[job.id for job in jobs], status=run.status
        )
    safe_publish(
        wiring.run_events,
        run.id,
        build_log_line(
            run_id=run.id,
            message=f"Submitted maintenance run with {len(jobs)} job(s)",
        ),
    )
    for job in jobs:
        session.refresh(job)
        wiring.queue.enqueue(str(job.id), str(job.destination_id))
        safe_publish(
            wiring.run_events,
            run.id,
            build_job_status(
                run_id=run.id,
                job_id=job.id,
                status=JobStatus.QUEUED.value,
                table_id=job.table_id,
                message="Job queued",
            ),
        )
        safe_publish(
            wiring.run_events,
            run.id,
            build_log_line(
                run_id=run.id,
                job_id=job.id,
                table_id=job.table_id,
                message=f"Queued maintenance job for table {job.table_id}",
            ),
        )
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
        raw_table_names = result.get("table_names", [])
        raw_schema_names = result.get("schema_names", [])
        table_ids = raw_table_ids if isinstance(raw_table_ids, list) else []
        table_names = raw_table_names if isinstance(raw_table_names, list) else []
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
                "table_names": [dict(item) for item in table_names],
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
    source_id: uuid.UUID | None = Query(default=None),  # noqa: B008 - uuid.UUID isn't in ruff's immutable-type allowlist; default is always None
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
    if source_id is not None:
        # Only scan runs record source_id in their summary (source/schemas/tables scope) --
        # fleet-wide "all" scans aren't tied to one lakehouse, so they're excluded here by design.
        stmt = stmt.where(MaintenanceRun.summary["source_id"].astext == str(source_id))
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


@router.get(
    "/runs/{run_id}/dispatches",
    dependencies=[Depends(require_viewer)],
    tags=["runs"],
)
def get_run_dispatches(run_id: uuid.UUID, session: SessionDep) -> dict[str, object]:
    """Native K8s Job dispatches for a run (cpu/mem requests, status, member jobs)."""
    from cryo.db.repositories.native_dispatch import dispatch_to_dict

    tenant_id = require_tenant(session)
    run_stmt = (
        select(MaintenanceRun)
        .join(MaintenanceJob, MaintenanceJob.run_id == MaintenanceRun.id)
        .join(Table, Table.id == MaintenanceJob.table_id)
        .where(MaintenanceRun.id == run_id)
    )
    if tenant_id.int != 0:
        run_stmt = run_stmt.where(Table.tenant_id == tenant_id)
    run = session.scalar(run_stmt.limit(1))
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    rows = session.scalars(
        select(NativeDispatch)
        .where(NativeDispatch.run_id == run_id)
        .order_by(NativeDispatch.created_at)
    ).all()
    return {"dispatches": [dispatch_to_dict(row) for row in rows]}


_TERMINAL_RUN_STATUSES = {
    "succeeded",
    "failed",
    "partial_failed",
    "cancelled",
}


def _snapshot_run_status(snapshot: dict[str, object]) -> str:
    run = snapshot.get("run")
    if not isinstance(run, dict):
        return ""
    status = run.get("status")
    return str(status) if status is not None else ""


async def _send_run_snapshot(
    websocket: WebSocket,
    session: Session,
    run_id: uuid.UUID,
) -> dict[str, object] | None:
    # expunge_all(), not expire_all(): this session is polled in a loop for as long as a
    # browser tab stays open on a run that hasn't reached a terminal status -- which, given
    # jobs can sit `queued` for a long time under Governor's admission pacing, can be a very
    # long time. expire_all() only expires attribute state; it doesn't remove objects from the
    # identity map, so the run/job/table/destination graph loaded via run_jobs_snapshot's
    # selectinload()s accumulates every poll instead of being replaced. Same identity-map leak
    # class fixed elsewhere this session (benefit_score.py, smart_maintenance_overview) --
    # confirmed live: cryo-api climbed to 1.7GB+ within 17 minutes and OOMKilled on a repeat
    # cycle, tracing back to a long-lived watch_run websocket connection.
    if hasattr(session, "expunge_all"):
        session.expunge_all()
    snapshot = datalake.run_jobs_snapshot(session, run_id)
    if snapshot is None:
        return None
    payload = RunSnapshotPayload.model_validate({"type": "run_snapshot", **snapshot})
    await websocket.send_json(jsonable_encoder(payload))
    return snapshot


@router.websocket("/ws/runs/{run_id}")
async def watch_run(
    websocket: WebSocket,
    run_id: uuid.UUID,
    token: Annotated[str | None, Query()] = None,
    once: Annotated[bool, Query()] = False,
    poll_interval_sec: Annotated[float, Query(ge=0.2, le=30.0)] = 2.0,
) -> None:
    principal = _websocket_viewer_principal(websocket, token)
    if principal is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    with SessionLocal() as session:
        bind_tenant(session, principal.tenant_id)
        await _watch_run_with_session(
            websocket,
            run_id,
            session,
            once=once,
            poll_interval_sec=poll_interval_sec,
        )


async def _watch_run_with_session(
    websocket: WebSocket,
    run_id: uuid.UUID,
    session: Session,
    *,
    once: bool,
    poll_interval_sec: float,
) -> None:
    try:
        snapshot = await _send_run_snapshot(websocket, session, run_id)
        if snapshot is None:
            await websocket.send_json({"type": "error", "detail": "Run not found"})
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        if once:
            await websocket.close()
            return

        run_events: Any = None
        events_available = False
        try:
            wiring = build_runtime_wiring()
            run_events = wiring.run_events
            events_available = hasattr(run_events, "replay_with_ids")
        except Exception:
            events_available = False

        if not events_available or run_events is None:
            await websocket.send_json(
                jsonable_encoder(
                    {
                        "type": "log_line",
                        "ts": datetime.now(UTC).isoformat(),
                        "run_id": str(run_id),
                        "level": "info",
                        "message": "Live log stream unavailable; showing polling snapshots.",
                    }
                )
            )
            while True:
                await asyncio.sleep(poll_interval_sec)
                snapshot = await _send_run_snapshot(websocket, session, run_id)
                if snapshot is None:
                    return
                run_status = _snapshot_run_status(snapshot)
                if run_status in _TERMINAL_RUN_STATUSES:
                    return
            return

        replayed = await asyncio.to_thread(run_events.replay_with_ids, run_id)
        last_event_id = "0-0"
        for message_id, event in replayed:
            await websocket.send_json(jsonable_encoder(event))
            last_event_id = message_id

        snapshot_ticks = 0
        snapshot_every = max(1, int(poll_interval_sec / 0.5))
        while True:
            rows = await asyncio.to_thread(
                run_events.read_blocking,
                run_id,
                last_id=last_event_id,
                block_ms=500,
                count=50,
            )
            for message_id, event in rows:
                await websocket.send_json(jsonable_encoder(event))
                last_event_id = message_id

            snapshot_ticks += 1
            if snapshot_ticks >= snapshot_every:
                snapshot_ticks = 0
                snapshot = await _send_run_snapshot(websocket, session, run_id)
                if snapshot is None:
                    return
                run_status = _snapshot_run_status(snapshot)
                if run_status in _TERMINAL_RUN_STATUSES:
                    # Drain any remaining events before closing.
                    rows = await asyncio.to_thread(
                        run_events.read_blocking,
                        run_id,
                        last_id=last_event_id,
                        block_ms=100,
                        count=50,
                    )
                    for message_id, event in rows:
                        await websocket.send_json(jsonable_encoder(event))
                        last_event_id = message_id
                    return
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
    now = datetime.now(UTC)
    if run.status != "cancelled":
        run.status = "cancelled"
        run.finished_at = now
    jobs = list(session.scalars(select(MaintenanceJob).where(MaintenanceJob.run_id == run_id)))
    active_dispatches = list(
        session.scalars(
            select(NativeDispatch).where(
                NativeDispatch.run_id == run_id,
                NativeDispatch.status.in_(("pending", "running", "cancel_requested", "cancelling")),
            )
        )
    )
    active_dispatch_ids = {dispatch.id for dispatch in active_dispatches}
    for dispatch in active_dispatches:
        if dispatch.status not in {"cancel_requested", "cancelling"}:
            dispatch.status = "cancel_requested"

    # (job_id, destination_id, prior_status, table_id, release_lock).
    # Native dispatch locks stay held until the watcher confirms pod deletion.
    release: list[tuple[str, str, str, str, bool]] = []
    cancellable = {
        JobStatus.QUEUED.value,
        JobStatus.ADMITTED.value,
        JobStatus.RUNNING.value,
        # DEFERRED jobs (e.g. "table_locked") never held the lock they're waiting on --
        # acquire_table_lock returned False, which is why they deferred in the first place --
        # so release_lock is a safe, ownership-checked no-op for them. Regression, 2026-07-28:
        # cancelling a run whose job was DEFERRED left the job row at status="deferred" even
        # though the run itself flipped to "cancelled" -- reconcile_deferred_jobs (cryo/queue/
        # reconcile.py) has no idea the parent run was cancelled and will happily re-admit and
        # dispatch that job on the next governor tick, silently un-cancelling a run the operator
        # explicitly stopped. Confirmed live: a cancelled Spark run's deferred job sat un-touched
        # for hours before this fix.
        JobStatus.DEFERRED.value,
    }
    for job in jobs:
        release_lock = job.dispatch_id not in active_dispatch_ids
        if job.status in cancellable:
            release.append(
                (
                    str(job.id),
                    str(job.destination_id),
                    job.status,
                    str(job.table_id),
                    release_lock,
                )
            )
            job.status = JobStatus.SKIPPED.value
            job.error = "cancelled"
            job.finished_at = run.finished_at or now
        elif job.status == JobStatus.SKIPPED.value and (job.error or "") == "cancelled":
            # Idempotent re-cancel: free leftover in-flight / table-lock from a prior
            # cancel that only cleared DB status (seen with run 888a7b41 locally).
            release.append(
                (
                    str(job.id),
                    str(job.destination_id),
                    JobStatus.RUNNING.value,
                    str(job.table_id),
                    release_lock,
                )
            )
    session.commit()
    # Release Redis queue / in-flight slots and per-table locks. Without this, cancel
    # leaves cryo:inflight:{destination} counters elevated and table locks held even
    # though no worker is running (inflight: bulk cancel of the 800+ cron backlog;
    # locks: cancelled native/adhoc runs blocking re-maintenance on the same table).
    if release:
        try:
            wiring = build_runtime_wiring()
            for job_id, destination_id, prior_status, table_id, release_lock in release:
                queue_status = wiring.queue.status(job_id)
                if prior_status == JobStatus.QUEUED.value or queue_status is JobStatus.QUEUED:
                    wiring.queue.cancel_pending(job_id, destination_id)
                elif queue_status in {JobStatus.ADMITTED, JobStatus.RUNNING}:
                    # ADMITTED / RUNNING held an in-flight slot via queue.admit().
                    wiring.queue.complete(job_id, JobStatus.SKIPPED)
                if release_lock:
                    # Safe if the job never held the lock (ownership-checked in Redis/Lua).
                    wiring.lock_store.release_table_lock(table_id, job_id)
        except Exception:
            logger.exception(
                "Failed to release Redis queue/lock state while cancelling run %s", run_id
            )
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


@router.post(
    "/jobs/{job_id}/force-admit",
    response_model=JobRead,
    dependencies=[Depends(require_operator)],
    tags=["runs"],
)
def force_admit_job(job_id: uuid.UUID, session: SessionDep) -> JobRead:
    """Bypass soft scheduling gates (cooldown, benefit/moop score, offpeak window,
    fleet budget) for one job and requeue it immediately.

    Does not bypass table locks, PII gates, or ABAC checks -- those protect against
    genuinely unsafe concurrent operations on a table, not scheduling policy. A job
    stuck on "table_locked" needs the lock to actually clear (or a lock-bug fix), not
    this endpoint.
    """
    job = session.get(MaintenanceJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in {JobStatus.QUEUED.value, JobStatus.DEFERRED.value}:
        raise HTTPException(
            status_code=409,
            detail=f"Job is {job.status}; force-admit only applies to queued or deferred jobs",
        )
    job.force_admit = True
    was_deferred = job.status == JobStatus.DEFERRED.value
    if was_deferred:
        job.status = JobStatus.QUEUED.value
        job.error = None
        job.finished_at = None
    session.commit()
    session.refresh(job)
    if was_deferred:
        wiring = build_runtime_wiring()
        wiring.queue.enqueue(str(job.id), str(job.destination_id))
    return JobRead.model_validate(presets.job_summary(job))


def _websocket_viewer_principal(websocket: WebSocket, token: str | None) -> Principal | None:
    auth_header = websocket.headers.get("authorization")
    token_value = token
    if auth_header:
        scheme, _, credentials = auth_header.partition(" ")
        if scheme.lower() == "bearer" and credentials:
            token_value = credentials

    if not token_value:
        return None
    principal = principal_from_token(token_value)
    if principal is None or not principal.can(Role.VIEWER):
        return None
    return principal


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


def _ensure_catalog_binding_sources(session: Session, config: dict[str, Any]) -> None:
    """Ensure every catalog_bindings.source_id refers to a scoped Cryo source."""
    from cryo.engines.trino.catalog_bindings import parse_catalog_bindings

    bindings = parse_catalog_bindings(config)
    if not bindings:
        return
    for binding in bindings:
        get_scoped_resource(session, Source, binding.source_id, resource_type="Source")


def _renderer_for(
    engine: EngineType,
) -> Callable[[MaintenancePlan], list[RenderedStatement]]:
    if engine is EngineType.TRINO:
        return render_trino
    if engine is EngineType.SPARK_EMR:
        return render_spark
    if engine is EngineType.NATIVE:
        return render_native
    raise HTTPException(status_code=400, detail=f"Unsupported engine: {engine}")


def _build_plan_for_table(
    session: Session,
    table_id: uuid.UUID,
    preset: Preset | None,
    mode: Mode,
) -> MaintenancePlan | None:
    """Reconstruct the MaintenancePlan the worker would execute for one table. Used to run the
    Plan Verifier at enqueue time. Returns None when there's not enough information (no preset
    or no parsed ops), which is treated as 'verifier skipped' rather than 'verifier rejected'."""
    if preset is None:
        return None
    raw_ops = preset.operations or []
    parsed_ops: list[Operation] = []
    for op in raw_ops:
        if isinstance(op, str):
            from cryo.domain.enums import OperationType

            try:
                parsed_ops.append(Operation(type=OperationType(op)))
            except ValueError:
                continue
        elif isinstance(op, dict):
            from cryo.domain.enums import OperationType

            try:
                parsed_ops.append(
                    Operation(
                        type=OperationType(str(op.get("type"))),
                        params=dict(op.get("params") or {}),
                    )
                )
            except ValueError:
                continue
    if not parsed_ops:
        return None
    # Match the order the worker will actually execute (build_plan sorts by CORRECT_ORDER),
    # so the verifier's operation_ordering invariant sees the real sequence.
    parsed_ops = sort_operations_canonically(parsed_ops, key=lambda op: op.type)
    table = session.get(Table, table_id)
    if table is None:
        return None
    # Include catalog so the plan verifier can disambiguate schema.table pairs that exist in
    # more than one source (cdp vs cdp_dev, etc.). Bare schema.table makes _resolve_table
    # hit MultipleResultsFound and 500 the enqueue path.
    from cryo.db.model_utils import qualified_table_name

    try:
        table_ref = qualified_table_name(table)
    except Exception:
        table_ref = f"{table.schema_name}.{table.table_name}"
    return MaintenancePlan(
        table_ref=table_ref,
        mode=mode,
        operations=tuple(parsed_ops),
        notes=("verifier-checked",),
    )


def _test_native_destination(
    config: dict[str, object],
    *,
    destination_id: str | None = None,
) -> DestinationTestResponse:
    if not is_native_available(config):
        return DestinationTestResponse(
            ok=False,
            message="cryo-engine binary not found; set binary_path or install cryo-engine on PATH",
        )
    probe_config = dict(config)
    if destination_id:
        probe_config["destination_id"] = destination_id
    snapshot = NativeEngine(probe_config).probe_capacity()
    in_flight_note = ""
    if snapshot.used > 0:
        in_flight_note = f"; {snapshot.used} subprocess(es) in flight"
    return DestinationTestResponse(
        ok=True,
        capacity=snapshot.capacity,
        used=snapshot.used,
        available=snapshot.available,
        message=f"cryo-engine binary found{in_flight_note}",
    )
