"""Observe Spark-on-K8s plan footprints without widening rewrite scope.

PDAE-8348: all cryo-worker pods OOMKilled in one ~90min window (limit 4Gi, steady-state
~150-220Mi). The Helm bump in 1253fe3 (4Gi→6Gi) is a safety margin only. The lead table
was ~2TB / ~30k files — partition-aware plans plus `table_data.partitions` on the worker
are the in-process blow-up, not Spark itself (compute already runs in a Job).

Outlier telemetry must not turn partition-scoped rewrites into repeated full-table calls.
"""

from __future__ import annotations

import json
import logging
import platform
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from cryo.domain.enums import OperationType
from cryo.domain.models import MaintenancePlan
from cryo.planner.planner import full_table_optimize_allowed
from cryo.planner.serialization import plan_to_dict

logger = logging.getLogger(__name__)

# Conservative vs the PDAE-8348 lead (1.97TB / 30,547 files). Below these, keep
# partition filters so Spark still scopes the rewrite.
OUTLIER_SIZE_GB = 500.0
OUTLIER_FILE_COUNT = 10_000
OUTLIER_PARTITION_COUNT = 2_000
OUTLIER_PREDICATE_COUNT = 256
OUTLIER_PLAN_JSON_BYTES = 256 * 1024


@dataclass(frozen=True)
class SparkPayloadFootprint:
    partition_count: int
    predicate_count: int
    plan_json_bytes: int
    stats_json_bytes: int
    rss_mib: float | None
    total_size_gb: float | None
    file_count: int | None
    outlier_reason: str | None


class SparkPlanScopeError(ValueError):
    pass


def process_rss_mib() -> float | None:
    """Peak RSS in MiB. Linux ru_maxrss is KiB; macOS/BSD is bytes."""
    try:
        import resource

        raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        divisor = 1024 if platform.system() == "Linux" else 1024 * 1024
        return round(raw / divisor, 1)
    except Exception:  # noqa: BLE001 - diagnostic only
        return None


def approx_json_bytes(payload: Any) -> int:
    try:
        return len(json.dumps(payload, default=str, separators=(",", ":")))
    except Exception:  # noqa: BLE001
        return 0


def _file_count(stats: Mapping[str, Any]) -> int | None:
    for key in ("total_files", "file_count", "live_files", "data_file_count"):
        value = stats.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
    return None


def _size_gb(stats: Mapping[str, Any]) -> float | None:
    value = stats.get("total_size_gb")
    if isinstance(value, (int, float)):
        return float(value)
    return None


def predicate_count(plan: MaintenancePlan) -> int:
    total = 0
    for operation in plan.operations:
        if operation.predicates:
            total += len(operation.predicates)
    return total


def footprint(
    *,
    plan: MaintenancePlan | None,
    stats: Mapping[str, Any] | None = None,
    partitions: Sequence[Mapping[str, Any]] | None = None,
) -> SparkPayloadFootprint:
    stats = stats or {}
    partitions = partitions or ()
    plan_bytes = approx_json_bytes(plan_to_dict(plan)) if plan is not None else 0
    preds = predicate_count(plan) if plan is not None else 0
    reason = outlier_reason(
        size_gb=_size_gb(stats),
        file_count=_file_count(stats),
        partition_count=len(partitions),
        predicate_count=preds,
        plan_json_bytes=plan_bytes,
    )
    return SparkPayloadFootprint(
        partition_count=len(partitions),
        predicate_count=preds,
        plan_json_bytes=plan_bytes,
        stats_json_bytes=approx_json_bytes(dict(stats)),
        rss_mib=process_rss_mib(),
        total_size_gb=_size_gb(stats),
        file_count=_file_count(stats),
        outlier_reason=reason,
    )


def outlier_reason(
    *,
    size_gb: float | None,
    file_count: int | None,
    partition_count: int,
    predicate_count: int,
    plan_json_bytes: int,
) -> str | None:
    if size_gb is not None and size_gb >= OUTLIER_SIZE_GB:
        return f"total_size_gb={size_gb:.1f}>={OUTLIER_SIZE_GB}"
    if file_count is not None and file_count >= OUTLIER_FILE_COUNT:
        return f"file_count={file_count}>={OUTLIER_FILE_COUNT}"
    if partition_count >= OUTLIER_PARTITION_COUNT:
        return f"partition_count={partition_count}>={OUTLIER_PARTITION_COUNT}"
    if predicate_count >= OUTLIER_PREDICATE_COUNT:
        return f"predicate_count={predicate_count}>={OUTLIER_PREDICATE_COUNT}"
    if plan_json_bytes >= OUTLIER_PLAN_JSON_BYTES:
        return f"plan_json_bytes={plan_json_bytes}>={OUTLIER_PLAN_JSON_BYTES}"
    return None


def validate_spark_plan_scope(
    plan: MaintenancePlan,
    *,
    stats: Mapping[str, Any] | None = None,
) -> None:
    unscoped_optimizes = [
        operation
        for operation in plan.operations
        if operation.type is OperationType.OPTIMIZE and not operation.predicates
    ]
    if len(unscoped_optimizes) > 1:
        raise SparkPlanScopeError(
            "Spark plan contains multiple unscoped optimize operations; "
            "refusing duplicate whole-table rewrites."
        )
    if not unscoped_optimizes:
        return
    if stats is None:
        raise SparkPlanScopeError(
            "Spark plan contains an unscoped optimize operation without scan size evidence."
        )
    operation = unscoped_optimizes[0]
    if full_table_optimize_allowed(stats, operation.params):
        return
    total_size_gb = _size_gb(stats)
    size_detail = "unknown" if total_size_gb is None else f"{total_size_gb:g} GB"
    chunk_size_gb = operation.params.get("chunk_size_gb", 100)
    raise SparkPlanScopeError(
        "Spark plan contains an unscoped optimize operation for a table with size "
        f"{size_detail}, outside chunk size budget {chunk_size_gb} GB."
    )


def log_spark_payload(
    *,
    stage: str,
    table_ref: str | None,
    footprint_snap: SparkPayloadFootprint,
    extra: str = "",
) -> None:
    logger.info(
        "spark-k8s payload %s table=%s rss=%s MiB plan_json_bytes=%d stats_json_bytes=%d "
        "partitions=%d predicates=%d size_gb=%s files=%s outlier=%s%s",
        stage,
        table_ref or "unknown",
        footprint_snap.rss_mib,
        footprint_snap.plan_json_bytes,
        footprint_snap.stats_json_bytes,
        footprint_snap.partition_count,
        footprint_snap.predicate_count,
        footprint_snap.total_size_gb,
        footprint_snap.file_count,
        footprint_snap.outlier_reason,
        f" {extra}" if extra else "",
    )
