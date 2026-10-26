"""Native engine operation routing — COW vs MOR and Go vs Rust."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cryo.capabilities import SupportStatus, capabilities_for_engine, capability_for
from cryo.domain.enums import EngineType, OperationType
from cryo.domain.models import MaintenancePlan, Operation

_ENGINE_IMPL_ENV = "CRYO_ENGINE_IMPL"

NATIVE_BASE_OPERATIONS: tuple[OperationType, ...] = tuple(
    cell.operation
    for cell in capabilities_for_engine(EngineType.NATIVE)
    if cell.support is SupportStatus.SUPPORTED
)

# Operations the native subprocess never executes itself. They route to a SQL
# fallback destination (Trino/Spark) when one is configured, otherwise they are
# recorded as skipped with the reason below. Fixes the silent-drop of
# COMPUTE_STATISTICS logged as bug #25 in docs/native-iceberg-rust-engine-errors.md.
NATIVE_ALWAYS_DISCONNECTED: tuple[OperationType, ...] = tuple(
    cell.operation
    for cell in capabilities_for_engine(EngineType.NATIVE)
    if cell.support is SupportStatus.FALLBACK_REQUIRED
)

NATIVE_MANIFESTS_DETAIL = (
    "optimize_manifests is not supported by the native engine (iceberg-go); "
    "route to a compatible Trino/Spark fallback"
)

NATIVE_COMPUTE_STATISTICS_DETAIL = (
    "compute_statistics (Puffin NDV + Bloom filters) not implemented in cryo-engine-go; "
    "route to Trino (ANALYZE) / Spark (CALL system.compute_table_stats)"
)

NATIVE_CONVERT_DELETES_DETAIL = (
    "convert_deletes requires SQL engine rewrite_data_files options; route to Spark/Trino fallback"
)

NATIVE_REWRITE_POSITION_DELETES_DETAIL = (
    "rewrite_position_delete_files (Okolnychyi minor delete compaction) has no cryo-engine-go "
    "implementation; route to Spark (Trino has no equivalent procedure either)"
)

NATIVE_ZORDER_DETAIL = (
    "z-order compaction is not supported in cryo-engine-go; route OPTIMIZE to Trino/Spark (Phase F)"
)

NATIVE_V3_PARTITIONED_DV_DETAIL = (
    "v3 partitioned deletion vectors not supported in pinned iceberg-go (#1135); "
    "route OPTIMIZE to Trino/Spark"
)

NATIVE_DELETE_FILES_DETAIL = (
    "rewrite (optimize) blocked: cryo.table_stats.total_delete_files > 0 "
    "(iceberg-go cannot rewrite tables with delete files); "
    "expire_snapshots and remove_orphan_files still run"
)

NATIVE_RUST_OPTIMIZE_DETAIL = (
    "native Rust compaction unavailable: iceberg-rust has no rewrite-files commit "
    "(apache/iceberg-rust#2269 closed unimplemented); "
    "set CRYO_ENGINE_IMPL=go or destination.config.engine_impl=go"
)

NATIVE_LEGACY_DISCONNECTED_DETAIL = (
    "native compaction/manifests disconnected; blocked upstream — "
    "Phase 3 task 7 / EPIC #2269, issue #2556"
)


@dataclass(frozen=True)
class NativeRoutingResult:
    """How a maintenance plan splits across native subprocess vs SQL fallback."""

    native_operations: tuple[Operation, ...]
    fallback_operations: tuple[tuple[Operation, str], ...]
    skipped_operations: tuple[tuple[Operation, str], ...]

    @property
    def has_native_work(self) -> bool:
        return bool(self.native_operations)

    @property
    def has_work(self) -> bool:
        return bool(self.native_operations or self.fallback_operations)


def is_go_engine_impl(config: Mapping[str, Any] | None = None) -> bool:
    """Return True when the worker should invoke cryo-engine-go."""
    cfg = config or {}
    configured = cfg.get("engine_impl")
    if isinstance(configured, str) and configured.strip():
        return configured.strip().lower() == "go"
    env_value = os.getenv(_ENGINE_IMPL_ENV, "").strip().lower()
    return env_value == "go"


def has_nonzero_delete_files(stats: Mapping[str, Any] | None) -> bool:
    """True when scanner stats report total_delete_files > 0 (column or raw blob)."""
    if not stats:
        return False
    total_delete_files = stats.get("total_delete_files")
    if total_delete_files is not None and int(total_delete_files) > 0:
        return True
    raw = stats.get("raw")
    if isinstance(raw, Mapping):
        raw_total = raw.get("total_delete_files")
        if raw_total is not None and int(raw_total) > 0:
            return True
    return False


def is_mor_table(stats: Mapping[str, Any] | None) -> bool:
    """Detect merge-on-read tables from scanner stats (position/equality delete files)."""
    if not stats:
        return False
    if has_nonzero_delete_files(stats):
        return True
    delete_file_pct = stats.get("delete_file_pct")
    if delete_file_pct is not None and float(delete_file_pct) > 0:
        return True
    raw = stats.get("raw")
    if isinstance(raw, Mapping):
        raw_pct = raw.get("delete_file_pct")
        if raw_pct is not None and float(raw_pct) > 0:
            return True
    return False


def _format_version(stats: Mapping[str, Any] | None) -> int:
    if not stats:
        return 2
    for key in ("format_version",):
        value = stats.get(key)
        if value is not None:
            return int(value)
    raw = stats.get("raw")
    if isinstance(raw, Mapping) and raw.get("format_version") is not None:
        return int(raw["format_version"])
    return 2


def _is_partitioned_table(stats: Mapping[str, Any] | None) -> bool:
    if not stats:
        return False
    partition_col = stats.get("partition_col")
    if isinstance(partition_col, str) and partition_col.strip() not in {"", "_unpartitioned"}:
        return True
    total_partitions = stats.get("total_partitions")
    if total_partitions is not None and int(total_partitions) > 1:
        return True
    raw = stats.get("raw")
    if isinstance(raw, Mapping):
        raw_partition_col = raw.get("partition_col")
        if isinstance(raw_partition_col, str) and raw_partition_col.strip() not in {
            "",
            "_unpartitioned",
        }:
            return True
        partition_fields = raw.get("partition_fields")
        if isinstance(partition_fields, list) and partition_fields:
            return True
    return False


def has_v3_partitioned_deletion_vectors(stats: Mapping[str, Any] | None) -> bool:
    """Gate native OPTIMIZE when v3 partitioned deletion vectors are present (#1135)."""
    if _format_version(stats) < 3:
        return False
    if not _is_partitioned_table(stats):
        return False
    raw = stats.get("raw") if stats else None
    if not isinstance(raw, Mapping):
        return False
    delete_files = raw.get("delete_files")
    if not isinstance(delete_files, list):
        return False
    for entry in delete_files:
        if not isinstance(entry, Mapping):
            continue
        file_path = str(entry.get("file_path", "")).lower()
        content = str(entry.get("content", "")).lower()
        if "deletion-vector" in file_path or "deletion_vector" in content:
            return True
    return False


def native_optimize_enabled(
    stats: Mapping[str, Any] | None,
    config: Mapping[str, Any] | None = None,
) -> bool:
    """OPTIMIZE is native-supported on the Go engine for COW tables only.

    Tables with total_delete_files > 0 are blocked: iceberg-go cannot reliably rewrite
    tables that have delete files. expire_snapshots / remove_orphan_files stay native.
    """
    if not is_go_engine_impl(config):
        return False
    if has_nonzero_delete_files(stats):
        return False
    return not has_v3_partitioned_deletion_vectors(stats)


def native_cow_optimize_enabled(
    stats: Mapping[str, Any] | None,
    config: Mapping[str, Any] | None = None,
) -> bool:
    """Backward-compatible alias: COW-only native optimize check."""
    if not native_optimize_enabled(stats, config):
        return False
    return not is_mor_table(stats)


def native_supported_operation_types(
    stats: Mapping[str, Any] | None,
    config: Mapping[str, Any] | None = None,
) -> frozenset[OperationType]:
    """Operation types eligible for the native subprocess for this table."""
    supported = set(NATIVE_BASE_OPERATIONS)
    if native_optimize_enabled(stats, config):
        supported.add(OperationType.OPTIMIZE)
    return frozenset(supported)


def native_fallback_supports(
    engine: EngineType | str,
    operation: OperationType,
) -> bool:
    """Return whether the manifest permits this destination as a native fallback."""
    try:
        engine_type = EngineType(engine)
    except ValueError:
        return False
    return engine_type in capability_for(EngineType.NATIVE, operation).fallback_engines


def native_fallback_mismatch_detail(
    engine: EngineType | str,
    operation: OperationType,
) -> str:
    cell = capability_for(EngineType.NATIVE, operation)
    allowed = ", ".join(item.value for item in cell.fallback_engines) or "none"
    engine_name = engine.value if isinstance(engine, EngineType) else engine
    return (
        f"configured fallback engine {engine_name!r} cannot execute {operation.value}; "
        f"compatible fallback engines: {allowed}"
    )


def _optimize_requires_sql_fallback(
    operation: Operation, stats: Mapping[str, Any] | None
) -> str | None:
    """Return a fallback detail when OPTIMIZE must not run on the Go subprocess."""
    params = operation.params or {}
    strategy = str(params.get("strategy", "")).strip().lower()
    zorder_columns = params.get("zorder_columns") or []
    if strategy == "zorder" or (isinstance(zorder_columns, list) and zorder_columns):
        return NATIVE_ZORDER_DETAIL
    if has_v3_partitioned_deletion_vectors(stats):
        return NATIVE_V3_PARTITIONED_DV_DETAIL
    return None


NATIVE_ENGINE_DISABLED_DETAIL = (
    "native (iceberg-go) engine is fleet-wide disabled (CRYO_NATIVE_ENGINE_ENABLED=false); "
    "route to Spark/Trino fallback"
)


def fallback_is_spark(engine: object) -> bool:
    """A disabled native destination may fall back to Spark, never to Trino."""
    return str(engine).lower() in ("spark_emr", "enginetype.spark_emr")


def _native_engine_globally_enabled() -> bool:
    from cryo.config import get_settings

    return get_settings().native_engine_enabled


def resolve_native_routing(
    plan: MaintenancePlan,
    stats: Mapping[str, Any] | None,
    config: Mapping[str, Any] | None = None,
) -> NativeRoutingResult:
    """Classify plan operations for native subprocess vs SQL fallback vs skip."""
    cfg = config or {}
    has_sql_fallback = _has_sql_fallback(cfg)
    native_ops: list[Operation] = []
    fallback_ops: list[tuple[Operation, str]] = []
    skipped_ops: list[tuple[Operation, str]] = []

    # Real incident, 2026-09-16: example_table's native-engine job reported
    # success=true with zero actual Iceberg mutation -- the trust boundary between Cryo and the
    # Go binary has no independent verification (see _reported_zero_work_operations in
    # cryo/engines/native/watcher.py for the narrower per-operation guard). Until that binary can
    # be trusted again, native_engine_enabled=False routes every operation through the exact same
    # tested SQL-fallback path already used for individual unsupported operations (below), rather
    # than inventing a new bypass -- every table with a configured sql_fallback_destination_id
    # (Spark-on-K8s) keeps running maintenance uninterrupted, just never against the native binary.
    if not _native_engine_globally_enabled():
        for operation in plan.operations:
            if has_sql_fallback:
                fallback_ops.append((operation, NATIVE_ENGINE_DISABLED_DETAIL))
            else:
                skipped_ops.append((operation, NATIVE_ENGINE_DISABLED_DETAIL))
        return NativeRoutingResult(
            native_operations=tuple(native_ops),
            fallback_operations=tuple(fallback_ops),
            skipped_operations=tuple(skipped_ops),
        )

    for operation in plan.operations:
        if operation.type in NATIVE_BASE_OPERATIONS:
            native_ops.append(operation)
            continue

        if operation.type is OperationType.OPTIMIZE:
            # iceberg-go cannot rewrite tables with delete files -- route to a SQL
            # fallback (Spark/Trino) when one is configured, same as every other
            # native-unsupported case below. Was a hard skip (never routed even with
            # sql_fallback set) until cryo.engines.spark.k8s landed and was proven to
            # actually execute rewrite_manifests/rewrite_data_files against a real
            # delete-heavy table (example-cluster, 2026-07-21) -- see
            # tests/test_native_admit.py and tests/test_spark_k8s*.py.
            if has_nonzero_delete_files(stats):
                if has_sql_fallback:
                    fallback_ops.append((operation, NATIVE_DELETE_FILES_DETAIL))
                else:
                    skipped_ops.append((operation, NATIVE_DELETE_FILES_DETAIL))
                continue
            sql_fallback_detail = _optimize_requires_sql_fallback(operation, stats)
            if sql_fallback_detail is not None:
                if has_sql_fallback:
                    fallback_ops.append((operation, sql_fallback_detail))
                else:
                    skipped_ops.append((operation, sql_fallback_detail))
                continue
            if native_optimize_enabled(stats, cfg):
                native_ops.append(operation)
                continue
            if has_v3_partitioned_deletion_vectors(stats):
                detail = NATIVE_V3_PARTITIONED_DV_DETAIL
            elif not is_go_engine_impl(cfg):
                detail = NATIVE_RUST_OPTIMIZE_DETAIL
            else:
                detail = NATIVE_LEGACY_DISCONNECTED_DETAIL
            if has_sql_fallback:
                fallback_ops.append((operation, detail))
            else:
                skipped_ops.append((operation, detail))
            continue

        if operation.type in NATIVE_ALWAYS_DISCONNECTED:
            if operation.type is OperationType.COMPUTE_STATISTICS:
                detail = NATIVE_COMPUTE_STATISTICS_DETAIL
            elif operation.type is OperationType.CONVERT_DELETES:
                detail = NATIVE_CONVERT_DELETES_DETAIL
            elif operation.type is OperationType.REWRITE_POSITION_DELETES:
                detail = NATIVE_REWRITE_POSITION_DELETES_DETAIL
            else:
                detail = NATIVE_MANIFESTS_DETAIL
            if has_sql_fallback:
                fallback_ops.append((operation, detail))
            else:
                skipped_ops.append((operation, detail))
            continue

        raise RuntimeError(
            f"Native capability routing has no behavior for operation {operation.type.value!r}"
        )

    return NativeRoutingResult(
        native_operations=tuple(native_ops),
        fallback_operations=tuple(fallback_ops),
        skipped_operations=tuple(skipped_ops),
    )


def filter_native_operations(
    plan: MaintenancePlan,
    stats: Mapping[str, Any] | None,
    config: Mapping[str, Any] | None = None,
) -> tuple[Operation, ...]:
    """Preserve planner order for operations executed in one native subprocess."""
    return resolve_native_routing(plan, stats, config).native_operations


def _has_sql_fallback(config: Mapping[str, Any]) -> bool:
    fallback_id = config.get("sql_fallback_destination_id") or config.get("fallback_destination_id")
    return isinstance(fallback_id, str) and bool(fallback_id.strip())
