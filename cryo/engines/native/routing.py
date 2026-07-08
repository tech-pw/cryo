"""Native engine operation routing — COW vs MOR and Go vs Rust."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cryo.domain.enums import OperationType
from cryo.domain.models import MaintenancePlan, Operation

_ENGINE_IMPL_ENV = "CRYO_ENGINE_IMPL"

NATIVE_BASE_OPERATIONS: tuple[OperationType, ...] = (
    OperationType.EXPIRE_SNAPSHOTS,
    OperationType.REMOVE_ORPHAN_FILES,
)

NATIVE_ALWAYS_DISCONNECTED: tuple[OperationType, ...] = (OperationType.OPTIMIZE_MANIFESTS,)

NATIVE_MANIFESTS_DETAIL = (
    "optimize_manifests not implemented in iceberg-go; route to Trino/Spark (Phase F)"
)

NATIVE_V3_PARTITIONED_DV_DETAIL = (
    "v3 partitioned deletion vectors not supported in pinned iceberg-go (#1135); "
    "route OPTIMIZE to Trino/Spark"
)

NATIVE_RUST_OPTIMIZE_DETAIL = (
    "native compaction blocked upstream on iceberg-rust (EPIC #2269); "
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


def is_mor_table(stats: Mapping[str, Any] | None) -> bool:
    """Detect merge-on-read tables from scanner stats (position/equality delete files)."""
    if not stats:
        return False
    total_delete_files = stats.get("total_delete_files")
    if total_delete_files is not None and int(total_delete_files) > 0:
        return True
    delete_file_pct = stats.get("delete_file_pct")
    if delete_file_pct is not None and float(delete_file_pct) > 0:
        return True
    raw = stats.get("raw")
    if isinstance(raw, Mapping):
        raw_total = raw.get("total_delete_files")
        if raw_total is not None and int(raw_total) > 0:
            return True
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
    """OPTIMIZE is native-supported on the Go engine (COW + MOR, except gated v3-partitioned DV)."""
    if not is_go_engine_impl(config):
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

    for operation in plan.operations:
        if operation.type in NATIVE_BASE_OPERATIONS:
            native_ops.append(operation)
            continue

        if operation.type is OperationType.OPTIMIZE:
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
            detail = NATIVE_MANIFESTS_DETAIL
            if has_sql_fallback:
                fallback_ops.append((operation, detail))
            else:
                skipped_ops.append((operation, detail))
            continue

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
    fallback_id = config.get("sql_fallback_destination_id") or config.get(
        "fallback_destination_id"
    )
    return isinstance(fallback_id, str) and bool(fallback_id.strip())
