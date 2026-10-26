from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any, Protocol
from uuid import UUID

from cryo.domain.enums import EngineType, Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.planner.planner import build_plan


class ExecutionDestination(Protocol):
    @property
    def engine(self) -> EngineType | str: ...

    @property
    def config(self) -> Mapping[str, Any]: ...


_PRESET_GATED_OPERATION_TYPES = (
    OperationType.EXPIRE_SNAPSHOTS,
    OperationType.REMOVE_ORPHAN_FILES,
    OperationType.OPTIMIZE,
    OperationType.OPTIMIZE_MANIFESTS,
)

# Delete-file rewrites and Puffin statistics are part of compaction: nobody who asked for
# "expire snapshots" or "remove orphan files" expects a data rewrite to ride along. Before
# this, a deep-mode run scoped to remove_orphan_files on a table with delete files also
# planned convert_deletes, because only the four gated types above were ever skipped.
_COMPACTION_FAMILY_OPERATION_TYPES = (
    OperationType.CONVERT_DELETES,
    OperationType.REWRITE_POSITION_DELETES,
    OperationType.COMPUTE_STATISTICS,
)


def build_execution_plan(
    *,
    stats: Mapping[str, Any],
    partitions: Sequence[Mapping[str, Any]],
    mode: Mode | str,
    params: Mapping[str, Any],
    destination: ExecutionDestination,
    operations: tuple[OperationType, ...] | None,
    protected_snapshot_ids: Sequence[int] = (),
    has_shared_location: bool = False,
    shared_location_unverified: bool = False,
    excluded_operations: Collection[str] = (),
) -> MaintenancePlan:
    plan_params = execution_plan_params(params, destination, stats)
    # Only an explicit request bounds the plan. A preset's operations list does not:
    # treating it as an allowlist dropped every op the table needed but the preset
    # omitted (2026-10-13). The request does the opposite — the caller named what
    # should run, and the planner adding expire_snapshots to an optimize request
    # submitted a procedure nobody asked for (issue #142, 2026-10-21).
    requested = params.get("requested_operations")
    if isinstance(requested, (list, tuple)) and requested:
        plan_params = {**plan_params, "skip_operations": _skips_for_request(requested)}
    if destination_is_native(destination):
        plan_params = inject_native_derived_chunk(
            plan_params,
            stats,
            destination,
            operations=operations,
        )
    plan = build_plan(
        stats,
        partitions,
        Mode(mode),
        plan_params,
        protected_snapshot_ids=protected_snapshot_ids,
        excluded_operations=excluded_operations,
    )
    plan = gate_orphan_removal_on_shared_location(
        plan,
        has_shared_location=has_shared_location,
        unverified=shared_location_unverified,
    )
    plan = gate_gc_disabled(plan, gc_disabled=gc_disabled_in_stats(stats))
    plan = gate_engine_unsupported(plan, destination=destination)
    plan = gate_trino_semantics(plan, destination=destination)
    return gate_native_optimize_manifests(
        plan,
        operations=operations,
        destination=destination,
    )


def destination_is_native(destination: ExecutionDestination) -> bool:
    engine = destination.engine
    if isinstance(engine, EngineType):
        return engine is EngineType.NATIVE
    return str(engine).lower() in {"native", "enginetype.native"}


def inject_native_derived_chunk(
    params: Mapping[str, Any],
    stats: Mapping[str, Any],
    destination: ExecutionDestination,
    *,
    operations: tuple[OperationType, ...] | None = None,
) -> dict[str, Any]:
    from cryo.engines.native.resources import (
        ResourceCeilings,
        derive_chunk_params,
        estimate_table_resources,
    )

    ceilings = ResourceCeilings.from_config(destination.config)
    base = {key: value for key, value in dict(params).items() if key != "chunk_size_gb"}
    estimate = estimate_table_resources(
        stats,
        operations=operations,
        params=base,
        ceilings=ceilings,
    )
    return derive_chunk_params(estimate.mem_mib, stats, base)


def execution_plan_params(
    params: Mapping[str, Any],
    destination: ExecutionDestination,
    stats: Mapping[str, Any],
) -> dict[str, Any]:
    from cryo.engines.trino.catalog_bindings import resolve_trino_catalog

    merged = dict(params)
    if destination_is_native(destination):
        merged.pop("chunk_size_gb", None)
    engine = destination.engine
    engine_value = engine.value if isinstance(engine, EngineType) else str(engine)
    source_catalog = stats.get("catalog") or stats.get("catalog_name")
    source_catalog_name = source_catalog if isinstance(source_catalog, str) else None
    source_id = stats.get("source_id")

    if engine_value == EngineType.TRINO.value:
        catalog = resolve_trino_catalog(
            destination.config,
            source_id=source_id if isinstance(source_id, (str, UUID)) else None,
            source_catalog_name=source_catalog_name,
        )
    else:
        catalog = destination.config.get("catalog") or destination.config.get("catalog_name")
        if not isinstance(catalog, str) or not catalog:
            catalog = None

    if not isinstance(catalog, str) or not catalog:
        return merged
    merged["catalog"] = catalog
    schema = stats.get("schema_name")
    table = stats.get("table_name")
    if schema and table:
        merged["table_ref"] = f"{catalog}.{schema}.{table}"
    return merged


def _skips_for_request(requested: Sequence[object]) -> list[str]:
    """Operations the planner must not add to an explicit request.

    The caller named what should run. Everything else is skipped, so a request for
    optimize cannot grow an expire_snapshots the table happens to qualify for.
    """
    from cryo.domain.enums import OperationType

    requested_names = {str(op) for op in requested}
    return [op.value for op in OperationType if op.value not in requested_names]


def skip_operations_from_preset(
    operations: tuple[OperationType, ...] | None,
) -> list[str]:
    """Deliberately always returns []: Preset.operations no longer force-skips anything.

    Until 2026-10, any operation type absent from a preset's `operations` list was force-
    skipped here regardless of what the table actually needed -- confirmed live, 2026-10-13:
    a table assigned the "default-spark-compact" preset (operations=["optimize"]) only ever
    got optimize, even on a deep-mode run, with expire_snapshots/remove_orphan_files/
    optimize_manifests silently dropped every single time by this allowlist, not by any
    per-table decision.

    build_plan() (cryo/planner/planner.py) already decides what a given table actually needs
    -- small-files threshold for optimize, delete-ratio policy for convert_deletes vs.
    rewrite_position_deletes, mode-gating for orphan/manifest work -- and already writes a
    plan.notes entry explaining every operation it skips. That per-table decision is the
    real gate now; a preset's operations list is no longer consulted as a second, blunter
    one layered on top of it. _PRESET_GATED_OPERATION_TYPES/_COMPACTION_FAMILY_OPERATION_TYPES
    above are kept only as a record of what used to be gated, not read by this function
    anymore -- do not wire them back in without re-reading the regression comment in
    planner.py's _apply_skip_operations, which is the reason this mechanism existed at all
    (Preset.operations looked like it controlled execution for over a month without actually
    doing so, until the 2026-08-03 fix made it a real gate -- this change removes it as a
    gate again, deliberately, not by accident this time).
    """
    return []


_SHARED_LOCATION_NOTE = (
    "Skipped remove_orphan_files: this table's S3 location overlaps another table's, "
    "so unreferenced-file deletion could remove the other table's live data. Give the "
    "table a unique location, then re-enable."
)
SHARED_LOCATION_UNVERIFIED_NOTE = (
    "Skipped remove_orphan_files: the shared-location check failed, so Cryo could not confirm "
    "this table's S3 location does not overlap another table's. Orphan removal runs again "
    "once the check succeeds."
)


def gate_orphan_removal_on_shared_location(
    plan: MaintenancePlan,
    *,
    has_shared_location: bool,
    unverified: bool = False,
) -> MaintenancePlan:
    """Drop remove_orphan_files when this table's S3 location overlaps another table's.

    ``unverified`` means the overlap lookup itself failed. That is treated as shared (fail
    closed, like the batch coordinator skipping orphan removal on a stale overlap snapshot)
    with its own plan note, so the run says why orphan removal was skipped.

    remove_orphan_files lists everything under the table root and deletes whatever is not
    referenced by *this* table's metadata. When two catalog entries share a warehouse path --
    exact same prefix, or one nested under the other -- the other table's live data files look
    exactly like orphans, and the procedure deletes them. This is not hypothetical:
    two catalog entries shared a warehouse prefix and orphan removal deleted the other
    table's files.

    Neither Trino's nor Spark's procedure takes a narrower prefix argument, and iceberg-go
    scans metadata.Location() (the whole table root, not just data/), so there is no way to
    run this safely on an overlapping table -- the only safe action is not to run it. Overlap
    detection already existed (cryo/anomalies/paths.py) but only produced a report; nothing
    consulted it before deleting files.

    The rest of the plan still runs: optimize, expire_snapshots, and optimize_manifests all
    operate through table metadata and never delete another table's files.
    """
    if not has_shared_location and not unverified:
        return plan
    if all(
        operation.type is not OperationType.REMOVE_ORPHAN_FILES for operation in plan.operations
    ):
        return plan
    return MaintenancePlan(
        table_ref=plan.table_ref,
        mode=plan.mode,
        operations=tuple(
            operation
            for operation in plan.operations
            if operation.type is not OperationType.REMOVE_ORPHAN_FILES
        ),
        notes=(
            *plan.notes,
            _SHARED_LOCATION_NOTE if has_shared_location else SHARED_LOCATION_UNVERIFIED_NOTE,
        ),
    )


_GC_OPERATION_TYPES = (OperationType.EXPIRE_SNAPSHOTS, OperationType.REMOVE_ORPHAN_FILES)


def gc_disabled_in_stats(stats: Mapping[str, Any]) -> bool:
    """True only when the table explicitly sets ``gc.enabled`` to something other than true.

    Iceberg reads the property with Java ``Boolean.parseBoolean``, so only a case-insensitive
    ``true`` keeps GC on. The scanner's typed ``gc_enabled`` is the fallback when the raw
    property was not captured.
    """
    raw = stats.get("raw")
    raw = raw if isinstance(raw, Mapping) else {}
    properties = raw.get("iceberg_properties")
    if isinstance(properties, Mapping) and "gc.enabled" in properties:
        return str(properties["gc.enabled"]).strip().lower() != "true"
    flag = raw.get("gc_enabled", stats.get("gc_enabled"))
    return flag is False


def gate_gc_disabled(plan: MaintenancePlan, *, gc_disabled: bool) -> MaintenancePlan:
    """Drop expire_snapshots and remove_orphan_files when the table sets ``gc.enabled=false``.

    Owners set it when another table shares this table's files (snapshot/migrate procedures),
    so deleting "unreferenced" files corrupts the other table. Spark and iceberg-go refuse
    these operations; Trino only honours the property from 484, so older Trino deletes anyway.
    """
    if not gc_disabled:
        return plan
    if all(operation.type not in _GC_OPERATION_TYPES for operation in plan.operations):
        return plan
    return MaintenancePlan(
        table_ref=plan.table_ref,
        mode=plan.mode,
        operations=tuple(
            operation for operation in plan.operations if operation.type not in _GC_OPERATION_TYPES
        ),
        notes=(
            *plan.notes,
            "Skipped expire_snapshots and remove_orphan_files: the table sets gc.enabled=false, "
            "which forbids deleting its files. Re-enable gc.enabled on the table to resume them.",
        ),
    )


_ENGINE_UNSUPPORTED: dict[str, dict[OperationType, str]] = {
    EngineType.TRINO.value: {
        OperationType.REWRITE_POSITION_DELETES: (
            "Skipped rewrite_position_deletes: Trino has no procedure for it. Route the table to "
            "a Spark destination to merge position deletes."
        ),
    },
}


def gate_engine_unsupported(
    plan: MaintenancePlan,
    *,
    destination: ExecutionDestination,
) -> MaintenancePlan:
    """Drop operations the destination engine cannot run, with a plan note per operation.

    The Trino renderer raises on rewrite_position_deletes rather than downgrading it to a full
    OPTIMIZE, so leaving it in the plan failed the whole run at render time.
    """
    engine = destination.engine
    engine_value = engine.value if isinstance(engine, EngineType) else str(engine).lower()
    unsupported = _ENGINE_UNSUPPORTED.get(engine_value)
    if not unsupported:
        return plan
    dropped = [op for op in plan.operations if op.type in unsupported]
    if not dropped:
        return plan
    return MaintenancePlan(
        table_ref=plan.table_ref,
        mode=plan.mode,
        operations=tuple(op for op in plan.operations if op.type not in unsupported),
        notes=(*plan.notes, *dict.fromkeys(unsupported[op.type] for op in dropped)),
    )


TRINO_PROTECTED_EXPIRE_NOTE = (
    "Skipped expire_snapshots: {count} snapshot(s) on this table are excluded from Cryo expiry, "
    "and Trino's expire_snapshots cannot keep a specific snapshot, so running it could delete "
    "them. Route the table to a Spark destination, which tags them first, or remove the "
    "exclusions."
)
TRINO_CONVERT_DELETES_NOTE = (
    "convert_deletes runs as OPTIMIZE on Trino: Trino has no delete-file-threshold rewrite, so "
    "this rewrites small data files and files with deletes across {scope}. It is planned and "
    "recorded as optimize."
)
TRINO_CONVERT_DELETES_REDUNDANT_NOTE = (
    "Dropped convert_deletes: on Trino it is the same table-wide OPTIMIZE this plan already runs."
)


def _engine_value(destination: ExecutionDestination) -> str:
    engine = destination.engine
    return engine.value if isinstance(engine, EngineType) else str(engine).lower()


def gate_trino_semantics(
    plan: MaintenancePlan,
    *,
    destination: ExecutionDestination,
) -> MaintenancePlan:
    """Make a Trino plan say what Trino will actually do (issue #198).

    - Trino's expire_snapshots has no way to keep a given snapshot id, so a table with
      protected snapshots skips expire on Trino, with a plan note, instead of silently
      expiring the snapshot the operator protected.
    - Trino has no convert-deletes procedure. The renderer runs it as OPTIMIZE, a data
      rewrite, so the plan, the cost estimate and the run record call it optimize. When the
      plan already has a table-wide optimize, the duplicate is dropped.
    """
    if _engine_value(destination) != EngineType.TRINO.value:
        return plan
    operations: list[Operation] = []
    notes: list[str] = []
    has_table_wide_optimize = any(
        op.type is OperationType.OPTIMIZE and not op.predicates for op in plan.operations
    )
    changed = False
    for op in plan.operations:
        if op.type is OperationType.EXPIRE_SNAPSHOTS and op.params.get("protected_snapshot_ids"):
            notes.append(
                TRINO_PROTECTED_EXPIRE_NOTE.format(count=len(op.params["protected_snapshot_ids"]))
            )
            changed = True
            continue
        if op.type is OperationType.CONVERT_DELETES:
            changed = True
            if has_table_wide_optimize and not op.predicates:
                notes.append(TRINO_CONVERT_DELETES_REDUNDANT_NOTE)
                continue
            scope = "the selected partitions" if op.predicates else "the whole table"
            notes.append(TRINO_CONVERT_DELETES_NOTE.format(scope=scope))
            operations.append(
                Operation(
                    OperationType.OPTIMIZE,
                    {**op.params, "planned_as": OperationType.CONVERT_DELETES.value},
                    op.predicates,
                )
            )
            has_table_wide_optimize = has_table_wide_optimize or not op.predicates
            continue
        operations.append(op)
    if not changed:
        return plan
    return MaintenancePlan(
        table_ref=plan.table_ref,
        mode=plan.mode,
        operations=tuple(operations),
        notes=(*plan.notes, *dict.fromkeys(notes)),
    )


def gate_native_optimize_manifests(
    plan: MaintenancePlan,
    *,
    operations: tuple[OperationType, ...] | None,
    destination: ExecutionDestination,
) -> MaintenancePlan:
    """Deliberately a no-op as of 2026-10-13: see skip_operations_from_preset's docstring.

    This used to strip optimize_manifests from a native-engine plan unless the preset's
    operations list explicitly named it -- the same preset-as-allowlist pattern removed
    there, just special-cased to one operation and one engine. No documented reason was
    found for why native/optimize_manifests needed a second, separate gate on top of
    build_plan's own mode-gating (optimize_manifests is deep-only already) -- kept as a
    pass-through, not deleted, so a real native-engine-specific constraint can be reinstated
    here deliberately if one turns out to exist, rather than rediscovering this function.
    """
    return plan
