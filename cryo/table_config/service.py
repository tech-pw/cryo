"""Build, preview and apply a table's configuration.

Property edits are metadata-only commits made through the table's Source catalog with
PyIceberg, the same path ``POST /tables/{id}/properties/apply`` already used. No engine runs
and no data is rewritten. Cryo-enforced settings are per-table preset overrides in Postgres.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from cryo.db.models import (
    AuditLog,
    Destination,
    Source,
    Table,
    TableAssignment,
    TableOperationExclusion,
    TableStats,
)
from cryo.domain.enums import Mode
from cryo.table_config import live
from cryo.table_config.context import (
    ConfigContext,
    bloom_columns,
    current_value,
    effective,
    is_true,
    parse_int,
)
from cryo.table_config.live import CatalogReadError, LiveTableConfig
from cryo.table_config.models import (
    ConfigChange,
    ConfigChangePreview,
    EngineNoteRead,
    MaintenanceOperationRead,
    PartitionFieldRead,
    PropertyDetailRead,
    RefRead,
    RiskDirectionRead,
    SettingEditorRead,
    SettingGroupRead,
    SortFieldRead,
    TableConfigResponse,
    TableMetadataRead,
    TableOperationsRead,
    TableSettingRead,
)
from cryo.table_config.operations import (
    OPERATIONS,
    TableOperationFacts,
    operation_state,
    preset_mode,
)
from cryo.table_config.property_docs import describe_property
from cryo.table_config.recommend import advise
from cryo.table_config.risk import Assessment, Risk, assess, max_risk, normalize
from cryo.table_config.settings import (
    BLOOM_FILTER_PREFIX,
    GROUPS,
    SETTINGS,
    SETTINGS_BY_KEY,
    SettingSpec,
    cryo_param_name,
)

MIN_REASON_LENGTH = 8


@dataclass(frozen=True)
class TableInputs:
    table: Table
    source: Source | None
    stats: TableStats | None
    assignment: TableAssignment | None
    preset_id: uuid.UUID | None
    preset_name: str | None
    preset_params: dict[str, Any]
    engine: str | None
    preset_mode: Mode | None = None
    operation_exclusions: tuple[TableOperationExclusion, ...] = ()
    held: bool = False
    shared_location: bool = False


class ConfigApplyError(Exception):
    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


def load_inputs(session: Session, table: Table) -> TableInputs:
    from cryo.anomalies.report import shared_location_table_ids
    from cryo.db.repositories import presets as preset_repo
    from cryo.db.repositories.exclusions import excluded_table_ids
    from cryo.db.repositories.operation_exclusions import list_operation_exclusions

    table = preset_repo.get_table_with_stats(session, table.id) or table
    source = session.get(Source, table.source_id) if table.source_id else None
    stats = session.get(TableStats, table.id)
    assignment = session.get(TableAssignment, table.id)
    effective_preset = preset_repo.resolve_effective_preset(session, table)
    preset_params: dict[str, Any] = {}
    engine: str | None = None
    preset_id: uuid.UUID | None = None
    preset_name: str | None = None
    mode: Mode | None = None
    if effective_preset is not None:
        mode = preset_mode(effective_preset.preset.mode)
        params = effective_preset.preset.params
        preset_params = dict(params) if isinstance(params, Mapping) else {}
        preset_name = effective_preset.preset.name
        preset_id = uuid.UUID(effective_preset.preset.id)
        destination = session.get(Destination, uuid.UUID(effective_preset.preset.destination_id))
        engine = str(destination.engine) if destination is not None else None
    return TableInputs(
        table=table,
        source=source,
        stats=stats,
        assignment=assignment,
        preset_id=preset_id,
        preset_name=preset_name,
        preset_params=preset_params,
        engine=engine,
        preset_mode=mode,
        operation_exclusions=tuple(list_operation_exclusions(session, table.id)),
        held=bool(excluded_table_ids(session, [table.id])),
        shared_location=table.id in shared_location_table_ids(session),
    )


def source_view(source: Source) -> dict[str, Any]:
    return {"name": source.name, "type": source.type, "config": dict(source.config or {})}


def read_live(inputs: TableInputs, *, use_cache: bool = True) -> LiveTableConfig:
    table = inputs.table
    if use_cache:
        hit = live.cached(table.id)
        if hit is not None:
            return hit
    if inputs.source is None:
        raise CatalogReadError("Table has no source catalog")
    config = live.read_live_config(source_view(inputs.source), table.schema_name, table.table_name)
    live.remember(table.id, config)
    return config


def build_context(inputs: TableInputs, config: LiveTableConfig | None) -> ConfigContext:
    stats = inputs.stats
    raw: dict[str, Any] = dict(stats.raw or {}) if stats is not None else {}
    overrides = inputs.assignment.param_overrides if inputs.assignment else None
    signals = {
        "commits_per_day": raw.get("commits_per_day"),
        "is_streaming": raw.get("is_streaming"),
        "total_snapshots": stats.total_snapshots if stats else None,
        "manifest_count": stats.manifest_count if stats else None,
        "delete_file_pct": stats.delete_file_pct if stats else None,
    }
    common: dict[str, Any] = {
        "cryo_engine": inputs.engine,
        "cryo_preset_name": inputs.preset_name,
        "preset_params": inputs.preset_params,
        "param_overrides": dict(overrides) if isinstance(overrides, Mapping) else {},
        "stats": signals,
    }
    if config is not None:
        return ConfigContext(
            properties=config.properties,
            format_version=config.format_version,
            partitioned=bool(config.partition_fields),
            has_sort_order=bool(config.sort_fields),
            identifier_fields=config.identifier_fields,
            columns=config.bloom_candidate_columns,
            column_count=config.column_count or None,
            refs=config.refs,
            **common,
        )
    scanned = raw.get("iceberg_properties")
    return ConfigContext(
        properties={str(k): str(v) for k, v in scanned.items()}
        if isinstance(scanned, dict)
        else {},
        format_version=parse_int(str(raw.get("format_version")))
        if raw.get("format_version")
        else None,
        partitioned=bool(stats and ((stats.total_partitions or 0) > 1 or stats.partition_col)),
        has_sort_order=bool(stats and stats.has_sort_order),
        **common,
    )


def build_response(
    inputs: TableInputs,
    config: LiveTableConfig | None,
    *,
    catalog_error: str | None = None,
) -> TableConfigResponse:
    ctx = build_context(inputs, config)
    can_edit_properties = config is not None
    settings = [_setting_read(spec, ctx, can_edit_properties, inputs) for spec in SETTINGS]
    return TableConfigResponse(
        table_id=inputs.table.id,
        read_from="catalog" if config is not None else "scan",
        read_at=datetime.now(UTC).isoformat(),
        catalog_error=catalog_error,
        can_edit=can_edit_properties,
        preset_name=inputs.preset_name,
        preset_engine=inputs.engine,
        groups=[
            SettingGroupRead(id=g.id, label=g.label, description=g.description) for g in GROUPS
        ],
        settings=settings,
        metadata=_metadata_read(config) if config is not None else None,
        properties=dict(sorted(ctx.properties.items())),
        property_details=_property_details(ctx.properties),
        columns=list(ctx.columns),
        operations=operations_read(inputs, ctx),
    )


_PROPERTY_SETTINGS: dict[str, tuple[str, str, str]] = {
    spec.key: (spec.key, spec.summary, spec.doc_url)
    for spec in SETTINGS
    if spec.kind in {"property", "format_version"}
}
_BLOOM_SETTING_KEY = next(spec.key for spec in SETTINGS if spec.kind == "bloom_columns")


def _property_details(properties: Mapping[str, str]) -> list[PropertyDetailRead]:
    out: list[PropertyDetailRead] = []
    for key, value in sorted(properties.items()):
        detail = describe_property(key, value, _PROPERTY_SETTINGS)
        setting_key = detail.setting_key
        if setting_key is None and key.startswith(BLOOM_FILTER_PREFIX):
            setting_key = _BLOOM_SETTING_KEY
        out.append(
            PropertyDetailRead(
                key=detail.key,
                value=detail.value,
                kind=detail.kind,
                summary=detail.summary,
                doc_url=detail.doc_url,
                setting_key=setting_key,
            )
        )
    return out


def operations_read(inputs: TableInputs, ctx: ConfigContext) -> TableOperationsRead:
    overrides = inputs.assignment.param_overrides if inputs.assignment else None
    params = {**inputs.preset_params, **(dict(overrides) if isinstance(overrides, Mapping) else {})}
    rows = {row.operation: row for row in inputs.operation_exclusions}
    gc = ctx.properties.get("gc.enabled")
    facts = TableOperationFacts(
        mode=inputs.preset_mode,
        preset_name=inputs.preset_name,
        params=params,
        excluded=rows.keys(),
        held=inputs.held,
        gc_disabled=gc is not None and gc.strip().lower() != "true",
        shared_location=inputs.shared_location,
        engine=inputs.engine,
    )
    operations: list[MaintenanceOperationRead] = []
    for spec in OPERATIONS:
        state = operation_state(spec, facts)
        row = rows.get(spec.operation.value)
        operations.append(
            MaintenanceOperationRead(
                operation=spec.operation.value,
                label=spec.label,
                phase=spec.phase,
                summary=spec.summary,
                when_planned=spec.when_planned,
                exclude_consequence=spec.exclude_consequence,
                exclude_risk=spec.exclude_risk,
                reasons_to_exclude=list(spec.reasons_to_exclude),
                companion_hint=spec.companion_hint,
                companion_operation=(
                    spec.companion_operation.value if spec.companion_operation else None
                ),
                engine_notes=[
                    EngineNoteRead(engine=engine, note=note) for engine, note in spec.engines
                ],
                doc_url=spec.doc_url,
                excluded=row is not None,
                excluded_by=row.created_by if row else None,
                excluded_at=row.created_at if row else None,
                excluded_reason=row.reason if row else None,
                status=state.status,
                status_detail=state.detail,
            )
        )
    return TableOperationsRead(
        preset_name=inputs.preset_name,
        mode=inputs.preset_mode.value if inputs.preset_mode else None,
        held=inputs.held,
        operations=operations,
    )


def _setting_read(
    spec: SettingSpec,
    ctx: ConfigContext,
    can_edit_properties: bool,
    inputs: TableInputs,
) -> TableSettingRead:
    value, source = current_value(spec, ctx)
    advice = advise(spec.key, ctx)
    eff = effective(spec, value)
    matches: bool | None = None
    recommendation_risk: Risk | None = None
    if advice.recommended is not None:
        matches = _same(spec, eff, advice.recommended)
        if not matches:
            recommendation_risk = assess(spec, value, advice.recommended, ctx).risk
    editable, read_only_reason = _editability(spec, ctx, can_edit_properties, inputs)
    directions = _risk_directions(spec, value, ctx) if spec.editable else []
    live_directions = [d for d in directions if d.blocked is None] or directions
    change_risk: Risk = (
        max_risk([d.risk for d in live_directions]) if spec.editable else spec.read_only_risk
    )
    return TableSettingRead(
        key=spec.key,
        label=spec.label,
        group=spec.group,
        kind=spec.kind,
        summary=spec.summary,
        takes_effect=spec.takes_effect,
        reversible=spec.reversible,
        doc_url=spec.doc_url,
        current_value=value,
        current_source=source,
        is_set=source in {"table", "cryo_override", "cryo_preset", "metadata"},
        default_value=spec.default,
        default_label=spec.default_label,
        recommended_value=advice.recommended,
        recommendation_reason=advice.reason,
        note=advice.note,
        matches_recommendation=matches,
        change_risk=change_risk,
        recommendation_risk=recommendation_risk,
        risk_directions=directions,
        editable=editable,
        read_only_reason=read_only_reason,
        min_role=spec.min_role,
        editor=SettingEditorRead(
            value_type=spec.value_type,
            options=list(spec.options),
            minimum=spec.minimum,
            maximum=spec.maximum,
            unit=spec.unit,
            allow_unset=spec.allow_unset,
        ),
        engine_notes=[EngineNoteRead(engine=n.engine, note=n.note) for n in spec.engine_notes],
    )


def _editability(
    spec: SettingSpec,
    ctx: ConfigContext,
    can_edit_properties: bool,
    inputs: TableInputs,
) -> tuple[bool, str | None]:
    if not spec.editable:
        return False, spec.read_only_reason
    if spec.kind == "cryo_param":
        if inputs.preset_name is None:
            return False, "No Cryo preset governs this table. Assign one first."
        return True, None
    if not can_edit_properties:
        return False, "The catalog could not be read, so the live value is unknown."
    return True, None


def _risk_directions(
    spec: SettingSpec, value: str | None, ctx: ConfigContext
) -> list[RiskDirectionRead]:
    candidates: list[tuple[str, str | None]] = []
    eff = effective(spec, value)
    if spec.value_type == "bool":
        toggled = "false" if is_true(eff) else "true"
        candidates.append(("Turn on" if toggled == "true" else "Turn off", toggled))
    elif spec.value_type == "enum":
        for option in spec.options:
            if not _same(spec, eff, option):
                label = (
                    f"Upgrade to v{option}" if spec.kind == "format_version" else f"Set to {option}"
                )
                if spec.kind == "format_version" and (parse_int(eff) or 0) > int(option):
                    continue
                candidates.append((label, option))
    elif spec.value_type in {"bytes", "int", "duration_ms", "duration_days"}:
        current = parse_int(eff)
        if spec.minimum is not None and (current is None or current > spec.minimum):
            candidates.append(
                ("Lower" if current is not None else "Set a limit", str(spec.minimum))
            )
        if spec.maximum is not None and current is not None and current < spec.maximum:
            candidates.append(("Raise", str(spec.maximum)))
    if spec.kind == "cryo_param" and cryo_param_name(spec.key) in ctx.param_overrides:
        candidates.append(("Remove override", None))
    elif spec.kind == "property" and spec.key in ctx.properties and spec.allow_unset:
        candidates.append(("Reset to default", None))
    out: list[RiskDirectionRead] = []
    for label, target in candidates:
        result = assess(spec, value, target, ctx)
        out.append(
            RiskDirectionRead(
                label=label,
                risk=result.risk,
                detail=result.warnings[0] if result.warnings else None,
                blocked=result.blocked,
            )
        )
    return out


def _same(spec: SettingSpec, left: str | None, right: str | None) -> bool:
    if spec.value_type in {"bytes", "int", "duration_ms", "duration_days"}:
        return parse_int(left) == parse_int(right)
    return (left or "").strip().lower() == (right or "").strip().lower()


def _metadata_read(config: LiveTableConfig) -> TableMetadataRead:
    return TableMetadataRead(
        format_version=config.format_version,
        table_uuid=config.table_uuid,
        location=config.location,
        metadata_location=config.metadata_location,
        last_updated_ms=config.last_updated_ms,
        current_snapshot_id=str(config.current_snapshot_id)
        if config.current_snapshot_id is not None
        else None,
        snapshot_count=config.snapshot_count,
        metadata_log_count=config.metadata_log_count,
        schema_id=config.schema_id,
        schema_count=config.schema_count,
        column_count=config.column_count,
        identifier_fields=list(config.identifier_fields),
        partition_fields=[
            PartitionFieldRead(name=f.name, source_column=f.source_column, transform=f.transform)
            for f in config.partition_fields
        ],
        spec_id=config.spec_id,
        spec_count=config.spec_count,
        sort_fields=[
            SortFieldRead(
                column=f.column,
                transform=f.transform,
                direction=f.direction,
                null_order=f.null_order,
            )
            for f in config.sort_fields
        ],
        sort_order_id=config.sort_order_id,
        refs=[
            RefRead(
                name=ref.name,
                type=ref.type,
                snapshot_id=str(ref.snapshot_id) if ref.snapshot_id is not None else None,
                max_ref_age_ms=ref.max_ref_age_ms,
                max_snapshot_age_ms=ref.max_snapshot_age_ms,
                min_snapshots_to_keep=ref.min_snapshots_to_keep,
                retained_forever=ref.name != "main"
                and ref.max_ref_age_ms is None
                and "history.expire.max-ref-age-ms" not in config.properties,
            )
            for ref in config.refs
        ],
        statistics_files=config.statistics_files,
        partition_statistics_files=config.partition_statistics_files,
    )


def preview(
    changes: Sequence[ConfigChange], ctx: ConfigContext, inputs: TableInputs
) -> list[ConfigChangePreview]:
    seen: set[str] = set()
    out: list[ConfigChangePreview] = []
    for change in changes:
        spec = SETTINGS_BY_KEY.get(change.key)
        if spec is None:
            out.append(_rejected(change.key, change.key, "Unknown setting"))
            continue
        if change.key in seen:
            out.append(_rejected(spec.key, spec.label, "The same setting appears twice"))
            continue
        seen.add(change.key)
        old, _ = current_value(spec, ctx)
        if not spec.editable:
            out.append(_rejected(spec.key, spec.label, spec.read_only_reason or "Read-only", old))
            continue
        if spec.kind == "cryo_param" and inputs.preset_name is None:
            out.append(_rejected(spec.key, spec.label, "No Cryo preset governs this table", old))
            continue
        new, error = normalize(spec, change.value, ctx)
        if error is not None:
            out.append(_rejected(spec.key, spec.label, error, old))
            continue
        if change.check_expected and not _same(spec, old, change.expected_value):
            out.append(
                _rejected(
                    spec.key,
                    spec.label,
                    "Changed since you loaded the page. Reload to see the current value.",
                    old,
                )
            )
            continue
        no_op = _is_no_op(spec, old, new, ctx)
        result = Assessment() if no_op else assess(spec, old, new, ctx)
        if spec.kind == "cryo_param" and inputs.assignment is None and not no_op:
            result.warnings.append(
                f"This table follows preset {inputs.preset_name} through a rule. Saving an "
                "override pins it to that preset."
            )
        out.append(
            ConfigChangePreview(
                key=spec.key,
                label=spec.label,
                kind=spec.kind,
                old_value=old,
                new_value=new,
                risk=result.risk,
                warnings=result.warnings,
                blocked=result.blocked,
                min_role=spec.min_role,
                no_op=no_op,
            )
        )
    return out


def _is_no_op(spec: SettingSpec, old: str | None, new: str | None, ctx: ConfigContext) -> bool:
    if spec.kind == "cryo_param":
        name = cryo_param_name(spec.key)
        if new is None:
            return name not in ctx.param_overrides
        return name in ctx.param_overrides and _same(spec, old, new)
    if spec.kind == "property" and new is None:
        return spec.key not in ctx.properties
    if spec.kind == "property":
        return spec.key in ctx.properties and _same(spec, old, new)
    return _same(spec, effective(spec, old), effective(spec, new))


def _rejected(key: str, label: str, reason: str, old: str | None = None) -> ConfigChangePreview:
    spec = SETTINGS_BY_KEY.get(key)
    return ConfigChangePreview(
        key=key,
        label=label,
        kind=spec.kind if spec else "property",
        old_value=old,
        new_value=None,
        risk="low",
        blocked=reason,
        min_role=spec.min_role if spec else "operator",
    )


@dataclass(frozen=True)
class CatalogWrite:
    set_properties: dict[str, str]
    remove_properties: list[str]
    format_version: int | None


def plan_catalog_write(previews: Sequence[ConfigChangePreview], ctx: ConfigContext) -> CatalogWrite:
    sets: dict[str, str] = {}
    removes: list[str] = []
    format_version: int | None = None
    for item in previews:
        spec = SETTINGS_BY_KEY[item.key]
        if spec.kind == "property":
            if item.new_value is None:
                removes.append(spec.key)
            else:
                sets[spec.key] = item.new_value
        elif spec.kind == "bloom_columns":
            before = set(bloom_columns(ctx.properties))
            after = set(filter(None, (item.new_value or "").split(",")))
            for column in sorted(after - before):
                sets[BLOOM_FILTER_PREFIX + column] = "true"
            for column in sorted(before - after):
                removes.append(BLOOM_FILTER_PREFIX + column)
        elif spec.kind == "format_version" and item.new_value is not None:
            format_version = int(item.new_value)
    return CatalogWrite(sets, removes, format_version)


def commit_catalog_write(inputs: TableInputs, write: CatalogWrite) -> None:
    if inputs.source is None:
        raise CatalogReadError("Table has no source catalog")
    view = source_view(inputs.source)
    table = inputs.table

    def _commit() -> None:
        iceberg_table = live.load_iceberg_table(view, table.schema_name, table.table_name)
        with iceberg_table.transaction() as txn:
            if write.format_version is not None:
                txn.upgrade_table_version(write.format_version)
            if write.set_properties:
                txn.set_properties(**write.set_properties)
            if write.remove_properties:
                txn.remove_properties(*write.remove_properties)

    live.run_bounded(
        _commit, description=f"commit properties on {table.schema_name}.{table.table_name}"
    )


def apply_overrides(
    session: Session, inputs: TableInputs, previews: Sequence[ConfigChangePreview]
) -> TableAssignment:
    from cryo.planner.guardrails import validate_params

    assignment = inputs.assignment
    overrides: dict[str, Any] = (
        dict(assignment.param_overrides)
        if assignment is not None and isinstance(assignment.param_overrides, Mapping)
        else {}
    )
    for item in previews:
        name = cryo_param_name(item.key)
        if item.new_value is None:
            overrides.pop(name, None)
        else:
            overrides[name] = int(item.new_value)
    errors = validate_params(overrides)
    if errors:
        raise ConfigApplyError(400, {"param_overrides": errors})
    if assignment is None:
        if inputs.preset_id is None:
            raise ConfigApplyError(
                400, "Table has no effective preset to override; assign one first"
            )
        assignment = TableAssignment(
            table_id=inputs.table.id, preset_id=inputs.preset_id, pinned=True
        )
        session.add(assignment)
    assignment.param_overrides = overrides
    return assignment


def refresh_scan_snapshot(stats: TableStats | None, config: LiveTableConfig) -> None:
    """Keep the scanner's copy in step so other pages don't show stale values until next scan."""
    if stats is None:
        return
    from cryo.scanner.analyze import tracked_iceberg_properties

    raw: dict[str, Any] = dict(stats.raw or {})
    raw["iceberg_properties"] = tracked_iceberg_properties(config.properties)
    gc = config.properties.get("gc.enabled")
    raw["gc_enabled"] = None if gc is None else gc.strip().lower() == "true"
    if config.format_version is not None:
        raw["format_version"] = config.format_version
    stats.raw = raw
    target = parse_int(config.properties.get("write.target-file-size-bytes"))
    stats.target_file_size_bytes = target
    stats.write_distribution_mode = config.properties.get("write.distribution-mode")
    stats.has_sort_order = bool(config.sort_fields)


def audit_change(
    session: Session,
    *,
    table_id: uuid.UUID,
    item: ConfigChangePreview,
    actor: str | None,
    reason: str | None,
    status: str,
    error: str | None = None,
) -> AuditLog:
    parts = [f"{item.key}: {_audit_value(item.old_value)} -> {_audit_value(item.new_value)}"]
    parts.append(f"risk={item.risk}")
    if reason:
        parts.append(f"reason={reason}")
    if error:
        parts.append(f"error={error}")
    entry = AuditLog(
        table_id=table_id,
        action="table_config_change",
        status=status,
        engine="cryo" if item.kind == "cryo_param" else "pyiceberg",
        detail=" ".join(parts)[:4000],
        actor=actor,
    )
    session.add(entry)
    return entry


def _audit_value(value: str | None) -> str:
    return "<unset>" if value is None else value


@dataclass(frozen=True)
class ApplyOutcome:
    applied: list[ConfigChangePreview]
    audit_ids: list[int]
    config: LiveTableConfig | None
    catalog_error: str | None


def apply_config(
    session: Session,
    inputs: TableInputs,
    changes: Sequence[ConfigChange],
    *,
    actor: str | None,
    is_admin: bool,
    confirm: bool,
    reason: str | None,
) -> ApplyOutcome:
    try:
        config = read_live(inputs, use_cache=False)
    except CatalogReadError as exc:
        raise ConfigApplyError(503, f"Catalog metadata read failed: {exc}") from exc
    ctx = build_context(inputs, config)
    previews = preview(changes, ctx, inputs)
    blocked = [p for p in previews if p.blocked]
    if blocked:
        raise ConfigApplyError(
            400, {"blocked": [{"key": p.key, "reason": p.blocked} for p in blocked]}
        )
    pending = [p for p in previews if not p.no_op]
    if not pending:
        raise ConfigApplyError(400, "Nothing to change: every value already matches.")
    if not is_admin and any(p.min_role == "admin" for p in pending):
        names = ", ".join(p.label for p in pending if p.min_role == "admin")
        raise ConfigApplyError(403, f"Admin role is required to change: {names}")
    if max_risk([p.risk for p in pending]) == "high":
        cleaned = (reason or "").strip()
        if not confirm or len(cleaned) < MIN_REASON_LENGTH:
            raise ConfigApplyError(
                400,
                "High-risk changes need confirm=true and a reason of at least "
                f"{MIN_REASON_LENGTH} characters.",
            )

    catalog_items = [p for p in pending if SETTINGS_BY_KEY[p.key].kind != "cryo_param"]
    cryo_items = [p for p in pending if SETTINGS_BY_KEY[p.key].kind == "cryo_param"]
    audit_entries: list[AuditLog] = []

    if catalog_items:
        write = plan_catalog_write(catalog_items, ctx)
        try:
            commit_catalog_write(inputs, write)
        except CatalogReadError as exc:
            for item in catalog_items:
                audit_change(
                    session,
                    table_id=inputs.table.id,
                    item=item,
                    actor=actor,
                    reason=reason,
                    status="FAILED",
                    error=str(exc)[:500],
                )
            session.commit()
            raise ConfigApplyError(502, f"Catalog commit failed: {exc}") from exc
        finally:
            live.forget(inputs.table.id)

    if cryo_items:
        apply_overrides(session, inputs, cryo_items)

    for item in pending:
        audit_entries.append(
            audit_change(
                session,
                table_id=inputs.table.id,
                item=item,
                actor=actor,
                reason=reason,
                status="OK",
            )
        )

    refreshed: LiveTableConfig | None = None
    catalog_error: str | None = None
    if catalog_items:
        try:
            refreshed = read_live(inputs, use_cache=False)
            refresh_scan_snapshot(inputs.stats, refreshed)
        except CatalogReadError as exc:
            catalog_error = f"Saved, but re-reading the catalog failed: {exc}"
    else:
        refreshed = config
    session.commit()
    return ApplyOutcome(
        applied=pending,
        audit_ids=[int(entry.id) for entry in audit_entries],
        config=refreshed,
        catalog_error=catalog_error,
    )
