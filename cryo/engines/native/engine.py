"""Native engine for Cryo.

Worker maintenance runs invoke the ``cryo-engine-go`` subprocess.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from cryo.config import Settings, get_settings
from cryo.domain.enums import EngineType, Mode, OperationType
from cryo.domain.models import CapacitySnapshot, MaintenancePlan, Operation
from cryo.engines.base import ExecCtx, ExecResult, RenderedStatement
from cryo.engines.native.checkpoint import execute_plan_per_chunk
from cryo.engines.native.job_dispatch import (
    execute_request_via_job,
)
from cryo.engines.native.payload import (
    build_execute_request,
    build_execute_request_for_operations,
    native_table_ref,
    operation_from_dict,
    operation_to_dict,
)
from cryo.engines.native.routing import (
    NATIVE_BASE_OPERATIONS,
    NATIVE_LEGACY_DISCONNECTED_DETAIL,
    filter_native_operations,
    native_supported_operation_types,
)
from cryo.engines.native.runner import (
    NativeExecuteResult,
    NativeOperationResult,
    execute_request,
    is_cryo_engine_available,
    resolve_binary_path,
)

logger = logging.getLogger(__name__)

# Base native ops always routed to the subprocess. OPTIMIZE is added dynamically for
# Go engine tables (COW + MOR; v3 partitioned DV gated — see routing.py).
# OPTIMIZE_MANIFESTS always routes to sql_fallback_destination_id (Phase F opt.1).
NATIVE_SUPPORTED_OPERATIONS = NATIVE_BASE_OPERATIONS

# Legacy constant kept for tests/docs; worker routing uses routing.py.
NATIVE_DISCONNECTED_OPERATIONS = (OperationType.OPTIMIZE_MANIFESTS,)
NATIVE_DISCONNECTED_DETAIL = NATIVE_LEGACY_DISCONNECTED_DETAIL


def is_native_available(config: Mapping[str, Any] | None = None) -> bool:
    """Return True when cryo-engine binary is available for worker execution."""
    return is_cryo_engine_available(config or {})


@dataclass
class NativeEngine:
    """Native subprocess engine for Iceberg maintenance (cryo-engine-go / cryo-engine)."""

    config: dict[str, Any]
    settings: Settings | None = None
    source_config: Mapping[str, Any] = field(default_factory=dict)
    table_stats: Mapping[str, Any] | None = None

    type: EngineType = EngineType.NATIVE

    def __post_init__(self) -> None:
        self.settings = self.settings or get_settings()
        self._slot_cap = int(self.config.get("slot_cap", self.settings.default_native_slot_cap))

    def with_source_config(self, source_config: Mapping[str, Any]) -> NativeEngine:
        return NativeEngine(
            config=self.config,
            settings=self.settings,
            source_config=dict(source_config),
            table_stats=self.table_stats,
        )

    def with_table_stats(self, table_stats: Mapping[str, Any] | None) -> NativeEngine:
        return NativeEngine(
            config=self.config,
            settings=self.settings,
            source_config=dict(self.source_config),
            table_stats=table_stats,
        )

    def probe_capacity(self) -> CapacitySnapshot:
        """Return slot capacity with optional queue in-flight when destination_id is set."""
        from cryo.governor.capacity import probe_native_capacity
        from cryo.queue.redis_queue import get_redis_client

        destination_id = self.config.get("destination_id")
        queue = None
        if destination_id:
            try:
                from cryo.queue.redis_queue import RedisMaintenanceQueue

                client = get_redis_client()
                if client is not None:
                    queue = RedisMaintenanceQueue(client)
            except Exception:  # noqa: BLE001 - optional Redis for API probes
                queue = None
        return probe_native_capacity(
            self.config,
            destination_id=str(destination_id) if destination_id else None,
            queue=queue,
        )

    def render(self, plan: MaintenancePlan) -> list[RenderedStatement]:
        supported_types = native_supported_operation_types(self.table_stats, self.config)
        operations = tuple(op for op in plan.operations if op.type in supported_types)
        for operation in plan.operations:
            if operation.type not in supported_types:
                logger.warning("Native engine skipping unsupported operation %s", operation.type)
        if not operations:
            return []
        if len(operations) == 1:
            operation = operations[0]
            return [
                RenderedStatement(
                    action=operation.type,
                    sql=f"NATIVE:{operation.type.value}:{native_table_ref(plan.table_ref)}",
                    engine=EngineType.NATIVE,
                    payload={
                        "operation": operation_to_dict(operation),
                        "table_ref": plan.table_ref,
                        "mode": plan.mode.value,
                    },
                )
            ]
        return [
            RenderedStatement(
                action=operations[0].type,
                sql=f"NATIVE:BATCH:{native_table_ref(plan.table_ref)}",
                engine=EngineType.NATIVE,
                payload={
                    "batch": True,
                    "operations": [operation_to_dict(operation) for operation in operations],
                    "table_ref": plan.table_ref,
                    "mode": plan.mode.value,
                },
            )
        ]

    def session_setup(self) -> list[str]:
        """No session setup needed for native engine."""
        return []

    def execute(self, stmt: RenderedStatement, ctx: ExecCtx | None = None) -> ExecResult:
        start = time.monotonic()
        if not stmt.sql.startswith("NATIVE:"):
            return ExecResult(
                ok=False,
                detail=stmt.sql,
                error="Invalid native statement",
                duration_ms=_elapsed_ms(start),
            )
        if stmt.payload.get("batch"):
            return self._execute_batch(stmt, ctx, _elapsed_ms(start))
        return self._execute_single(stmt, ctx, start)

    def execute_plan(self, plan: MaintenancePlan, ctx: ExecCtx | None = None) -> list[ExecResult]:
        """Run all plan operations, either per-chunk (checkpointing) or bundled."""
        start = time.monotonic()

        if ctx is not None and ctx.chunk_checkpointing and ctx.session_factory is not None:
            return self._execute_plan_per_chunk(plan, ctx, start)

        return self._execute_plan_bundled(plan, ctx, start)

    def _execute_plan_bundled(
        self,
        plan: MaintenancePlan,
        ctx: ExecCtx | None,
        start: float,
    ) -> list[ExecResult]:
        """Run all plan operations in one cryo-engine subprocess (legacy path)."""
        if not self.source_config:
            error = "Native engine requires table source catalog config at execution time"
            return [
                ExecResult(
                    ok=False,
                    outcome_known=False,
                    detail=operation.type.value,
                    error=error,
                    duration_ms=_elapsed_ms(start),
                )
                for operation in plan.operations
            ]
        if resolve_binary_path(self.config) is None:
            error = "cryo-engine binary not found (set destination.config.binary_path)"
            return [
                ExecResult(
                    ok=False,
                    detail=operation.type.value,
                    error=error,
                    duration_ms=_elapsed_ms(start),
                )
                for operation in plan.operations
            ]

        operations = _supported_operations(plan, self.table_stats, self.config)
        if not operations:
            return []

        try:
            request = build_execute_request_for_operations(
                plan=plan,
                operations=operations,
                source_config=self.source_config,
                destination_config=self.config,
            )
            use_k8s = bool(self.config.get("k8s_dispatch", False))
            timeout_seconds = int(self.config.get("timeout_seconds", 3600))

            if use_k8s:
                from cryo.engines.native.resources import (
                    ResourceCeilings,
                    estimate_table_resources,
                )

                ceilings = ResourceCeilings.from_config(self.config)
                estimate = estimate_table_resources(
                    self.table_stats,
                    operations=operations,
                    ceilings=ceilings,
                )
                result = execute_request_via_job(
                    request,
                    config=self.config,
                    cpu_milli=estimate.cpu_milli,
                    mem_mib=estimate.mem_mib,
                    timeout_seconds=timeout_seconds,
                )
            else:
                result = execute_request(
                    request,
                    config=self.config,
                    timeout_seconds=timeout_seconds,
                    on_progress=_progress_callback(ctx),
                )
        except Exception as exc:
            error = f"Native execution failed: {exc}"
            return [
                ExecResult(
                    ok=False,
                    detail=operation.type.value,
                    error=error,
                    duration_ms=_elapsed_ms(start),
                )
                for operation in operations
            ]

        return _exec_results_from_native_batch(operations, result, _elapsed_ms(start))

    def _execute_plan_per_chunk(
        self,
        plan: MaintenancePlan,
        ctx: ExecCtx,
        start: float,
    ) -> list[ExecResult]:
        """Run plan operations one chunk at a time, checkpointing each commit."""
        if not self.source_config:
            error = "Native engine requires table source catalog config at execution time"
            return [
                ExecResult(
                    ok=False,
                    detail=operation.type.value,
                    error=error,
                    duration_ms=_elapsed_ms(start),
                )
                for operation in plan.operations
            ]
        if resolve_binary_path(self.config) is None:
            error = "cryo-engine binary not found (set destination.config.binary_path)"
            return [
                ExecResult(
                    ok=False,
                    detail=operation.type.value,
                    error=error,
                    duration_ms=_elapsed_ms(start),
                )
                for operation in plan.operations
            ]

        operations = _supported_operations(plan, self.table_stats, self.config)
        if not operations:
            return []

        run_id = ctx.run_id or str(plan.table_ref)
        session_factory = ctx.session_factory

        chunk_results = execute_plan_per_chunk(
            plan=plan,
            operations=operations,
            source_config=dict(self.source_config),
            destination_config=self.config,
            run_id=run_id,
            session_factory=session_factory,
            on_progress=_progress_callback(ctx),
            table_id=str(ctx.table_id) if ctx.table_id is not None else None,
        )

        exec_results: list[ExecResult] = []
        for chunk in chunk_results:
            op = chunk["operation"]
            native_result = chunk["native_result"]

            if chunk["skipped"]:
                exec_results.append(
                    ExecResult(
                        ok=True,
                        skipped=True,
                        detail="already checkpointed",
                        duration_ms=0,
                    )
                )
            elif chunk["ok"]:
                exec_results.append(
                    _operation_result_to_exec_result(
                        detail=op.type.value,
                        op_result=native_result,
                        result=_native_result_from_op_result(native_result),
                        duration_ms=chunk["duration_ms"],
                    )
                )
            else:
                exec_results.append(
                    ExecResult(
                        ok=False,
                        outcome_known=False,
                        detail=chunk["error"] or "chunk execution failed",
                        error=chunk["error"],
                        duration_ms=chunk["duration_ms"],
                    )
                )
        return exec_results

    def _execute_single(
        self,
        stmt: RenderedStatement,
        ctx: ExecCtx | None,
        start: float,
    ) -> ExecResult:
        if not self.source_config:
            return ExecResult(
                ok=False,
                outcome_known=False,
                detail=stmt.sql,
                error="Native engine requires table source catalog config at execution time",
                duration_ms=_elapsed_ms(start),
            )
        if resolve_binary_path(self.config) is None:
            return ExecResult(
                ok=False,
                detail=stmt.sql,
                error="cryo-engine binary not found (set destination.config.binary_path)",
                duration_ms=_elapsed_ms(start),
            )

        operation = _operation_from_payload(stmt.payload)
        if operation is None:
            return ExecResult(
                ok=False,
                detail=stmt.sql,
                error="Native statement missing operation payload",
                duration_ms=_elapsed_ms(start),
            )

        plan = MaintenancePlan(
            table_ref=str(stmt.payload.get("table_ref", "")),
            mode=Mode(str(stmt.payload.get("mode", Mode.DEEP.value))),
            operations=(operation,),
        )
        try:
            request = build_execute_request(
                plan=plan,
                operation=operation,
                source_config=self.source_config,
                destination_config=self.config,
            )
            result = execute_request(
                request,
                config=self.config,
                timeout_seconds=int(self.config.get("timeout_seconds", 3600)),
                on_progress=_progress_callback(ctx),
            )
        except Exception as exc:
            return ExecResult(
                ok=False,
                detail=stmt.sql,
                error=f"Native execution failed: {exc}",
                duration_ms=_elapsed_ms(start),
            )

        return _exec_result_from_native(stmt, result, _elapsed_ms(start))

    def _execute_batch(
        self,
        stmt: RenderedStatement,
        ctx: ExecCtx | None,
        elapsed_ms: int,
    ) -> ExecResult:
        plan = _plan_from_batch_payload(stmt.payload)
        if plan is None:
            return ExecResult(
                ok=False,
                detail=stmt.sql,
                error="Native batch statement missing operations payload",
                duration_ms=elapsed_ms,
            )
        results = self.execute_plan(plan, ctx)
        if not results:
            return ExecResult(
                ok=False,
                detail=stmt.sql,
                error="Native batch execution produced no results",
                duration_ms=elapsed_ms,
            )
        if len(results) == 1:
            return results[0]
        if any(not result.ok and not result.skipped for result in results):
            failed = next(result for result in results if not result.ok and not result.skipped)
            return ExecResult(
                ok=False,
                detail=failed.detail or failed.error or stmt.sql,
                error=failed.error or failed.detail,
                duration_ms=sum(result.duration_ms or 0 for result in results),
            )
        return ExecResult(
            ok=True,
            detail=stmt.sql,
            duration_ms=sum(result.duration_ms or 0 for result in results),
            skipped=all(result.skipped for result in results),
        )


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _operation_from_payload(payload: Mapping[str, object]) -> Operation | None:
    raw = payload.get("operation")
    if not isinstance(raw, dict):
        return None
    return operation_from_dict(raw)


def _is_unsupported_native_error(error: str | None) -> bool:
    if not error:
        return False
    lowered = error.lower()
    if "blocked upstream" in lowered:
        return True
    if "not available on iceberg-go" in lowered or "not implemented upstream" in lowered:
        return True
    if "route optimize_manifests to trino/spark" in lowered:
        return True
    return "not yet implemented" in lowered and "rewrite_data_files commit blocked" in lowered


def _supported_operations(
    plan: MaintenancePlan,
    stats: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> tuple[Operation, ...]:
    return filter_native_operations(plan, stats, config)


def _plan_from_batch_payload(payload: Mapping[str, object]) -> MaintenancePlan | None:
    raw_operations = payload.get("operations")
    if not isinstance(raw_operations, list):
        return None
    operations: list[Operation] = []
    for item in raw_operations:
        if not isinstance(item, dict):
            continue
        operation = operation_from_dict(item)
        if operation is not None:
            operations.append(operation)
    if not operations:
        return None
    return MaintenancePlan(
        table_ref=str(payload.get("table_ref", "")),
        mode=Mode(str(payload.get("mode", Mode.DEEP.value))),
        operations=tuple(operations),
    )


def _progress_callback(ctx: ExecCtx | None) -> Any:
    if ctx is None or ctx.on_progress is None:
        return None
    return ctx.on_progress


def _operation_result_to_exec_result(
    *,
    detail: str,
    op_result: NativeOperationResult | None,
    result: NativeExecuteResult,
    duration_ms: int,
) -> ExecResult:
    if (
        op_result is not None
        and not op_result.success
        and _is_unsupported_native_error(op_result.error)
    ):
        return ExecResult(
            ok=True,
            skipped=True,
            detail=op_result.error or detail,
            duration_ms=op_result.duration_ms or duration_ms,
            engine_ref=op_result.operation,
            input_files_count=op_result.input_files,
            output_files_count=op_result.output_files,
            actual_bytes_freed=op_result.bytes_removed,
            actual_bytes_written=op_result.bytes_written,
        )
    if not result.ok or op_result is None or not op_result.success:
        error_detail = result.error or (op_result.error if op_result else None) or detail
        return ExecResult(
            ok=False,
            detail=error_detail,
            error=error_detail,
            duration_ms=op_result.duration_ms if op_result else duration_ms,
            input_files_count=op_result.input_files if op_result else None,
            output_files_count=op_result.output_files if op_result else None,
            actual_bytes_freed=op_result.bytes_removed if op_result else None,
            actual_bytes_written=op_result.bytes_written if op_result else None,
            snapshots_expired=op_result.snapshots_expired if op_result else None,
            manifests_rewritten=op_result.manifests_rewritten if op_result else None,
        )
    return ExecResult(
        ok=True,
        detail=result.stdout or detail,
        duration_ms=op_result.duration_ms,
        input_files_count=op_result.input_files,
        output_files_count=op_result.output_files,
        actual_bytes_freed=op_result.bytes_removed,
        actual_bytes_written=op_result.bytes_written,
        snapshots_expired=op_result.snapshots_expired,
        manifests_rewritten=op_result.manifests_rewritten,
        engine_ref=op_result.operation,
    )


def _native_batch_failure_index(operations: tuple[Operation, ...]) -> int:
    """Index to attribute a silent subprocess death to.

    When cryo-engine is OOM-killed mid-batch it returns no per-op results. The
    worker audits the first ``ok=False`` result, so blaming index 0 mislabels
    the failure as ``expire_snapshots``. Prefer OPTIMIZE (rewrite) when present.
    """
    for index, operation in enumerate(operations):
        if operation.type is OperationType.OPTIMIZE:
            return index
    return max(0, len(operations) - 1)


def _exec_results_from_empty_native_batch(
    operations: tuple[Operation, ...],
    *,
    error: str,
    duration_ms: int,
) -> list[ExecResult]:
    """Map a dead/empty subprocess response onto per-op ExecResults.

    Only the attributed operation is FAILED; earlier ops are SKIPPED (status
    unknown) and later ops are SKIPPED (not executed) so audit/logs do not
    pin an OOM on expire/orphan.
    """
    if not operations:
        return []
    fail_idx = _native_batch_failure_index(operations)
    failed_name = operations[fail_idx].type.value
    exec_results: list[ExecResult] = []
    for index, operation in enumerate(operations):
        if index < fail_idx:
            exec_results.append(
                ExecResult(
                    ok=True,
                    skipped=True,
                    detail=(
                        f"native subprocess exited before results; "
                        f"{operation.type.value} status unknown"
                    ),
                    duration_ms=duration_ms,
                )
            )
        elif index == fail_idx:
            exec_results.append(
                ExecResult(
                    ok=False,
                    detail=operation.type.value,
                    error=error,
                    duration_ms=duration_ms,
                )
            )
        else:
            exec_results.append(
                ExecResult(
                    ok=True,
                    skipped=True,
                    detail=f"not executed; native subprocess failed on {failed_name}",
                    duration_ms=0,
                )
            )
    return exec_results


def _exec_results_from_native_batch(
    operations: tuple[Operation, ...],
    result: NativeExecuteResult,
    duration_ms: int,
) -> list[ExecResult]:
    if not result.results:
        error = result.error or "cryo-engine returned no operation results"
        return _exec_results_from_empty_native_batch(
            operations, error=error, duration_ms=duration_ms
        )

    results_by_operation = {item.operation: item for item in result.results}
    exec_results: list[ExecResult] = []
    for operation in operations:
        native_type = {
            OperationType.EXPIRE_SNAPSHOTS: "expire_snapshots",
            OperationType.REMOVE_ORPHAN_FILES: "remove_orphan_files",
            OperationType.OPTIMIZE: "rewrite_data_files",
            OperationType.OPTIMIZE_MANIFESTS: "rewrite_manifests",
            OperationType.COMPUTE_STATISTICS: "compute_statistics",
        }[operation.type]
        op_result = results_by_operation.get(native_type)
        if op_result is None and result.results:
            op_result = result.results[min(len(exec_results), len(result.results) - 1)]
        exec_results.append(
            _operation_result_to_exec_result(
                detail=operation.type.value,
                op_result=op_result,
                result=result,
                duration_ms=duration_ms,
            )
        )
    return exec_results


def _exec_result_from_native(
    stmt: RenderedStatement,
    result: NativeExecuteResult,
    duration_ms: int,
) -> ExecResult:
    op_result = result.results[0] if result.results else None
    return _operation_result_to_exec_result(
        detail=stmt.sql,
        op_result=op_result,
        result=result,
        duration_ms=duration_ms,
    )


def _native_result_from_op_result(
    op_result: NativeOperationResult | None,
) -> NativeExecuteResult:
    """Wrap a single NativeOperationResult into a NativeExecuteResult for the helper."""
    if op_result is None:
        return NativeExecuteResult(
            ok=False,
            table_ref="",
            results=(),
            error="no result",
        )
    return NativeExecuteResult(
        ok=op_result.success,
        table_ref="",
        results=(op_result,),
        error=op_result.error if not op_result.success else None,
    )
