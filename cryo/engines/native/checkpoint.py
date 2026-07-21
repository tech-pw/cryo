"""Per-chunk native execution with checkpointing.

Mirrors Iceberg's own PARTIAL_PROGRESS_ENABLED precedent: each chunk commits
independently, so a failure -- commit-race loss, spot interruption, node failure --
only forfeits that chunk's work. Resume is achieved by querying ChunkCheckpoint
to skip already-committed chunks.

Design constraints from docs/native-engine-scoping-research.md §6.4 and
docs/native-iceberg-go-compaction-plan.md §6:
- Partition-disjoint invariant: greedy_pack produces chunks that never touch the
  same partition, so chunks cannot conflict on commit.
- Per-chunk atomic commit: the Go engine already commits each rewrite_data_files
  as one atomic Iceberg snapshot; this module only adds the Python-side dispatch
  and checkpointing layer.
- Spot-instance eligibility: partial commits mean interrupted runs don't pay for
  already-finished chunks on retry.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from cryo.db.repositories.chunk_checkpoint import (
    get_checkpointed_chunks,
    write_checkpoint,
)
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.native.payload import build_execute_request
from cryo.engines.native.runner import (
    NativeExecuteResult,
    NativeOperationResult,
    execute_request,
    is_commit_race_lost,
)

logger = logging.getLogger(__name__)

_CHUNK_TIMEOUT_SECONDS = 1800


def execute_plan_per_chunk(
    plan: MaintenancePlan,
    operations: tuple[Operation, ...],
    source_config: dict[str, Any],
    destination_config: dict[str, Any],
    run_id: str,
    session_factory: Any,
    on_progress: Any = None,
    *,
    table_id: str | None = None,
) -> list[dict[str, Any]]:
    """Execute a maintenance plan one chunk at a time, checkpointing each commit.

    Args:
        plan: The full MaintenancePlan produced by the planner.
        operations: Filtered operations to execute (one per chunk).
        source_config: Source catalog config for the Go engine.
        destination_config: Destination/engine config.
        run_id: Run UUID, used as the ChunkCheckpoint key.
        session_factory: SQLAlchemy session factory for checkpoint writes.
        on_progress: Optional progress callback, called per chunk.
        table_id: Optional table UUID for multi-table-run checkpoint isolation.

    Returns:
        List of per-chunk result dicts with keys:
        - chunk_index (int)
        - ok (bool)
        - operation (Operation)
        - native_result (NativeOperationResult | None)
        - skipped (bool) — already checkpointed
        - duration_ms (int)
        - error (str | None)

    Each committed chunk writes a ChunkCheckpoint row before returning,
    so any mid-run failure can resume by calling this function again with
    the same run_id: already-committed chunks are skipped.
    """
    results: list[dict[str, Any]] = []
    total_duration_ms = 0

    with session_factory() as session:
        checkpointed = get_checkpointed_chunks(session, run_id, table_id=table_id)

    for idx, operation in enumerate(operations):
        start = time.monotonic()
        chunk_duration_ms = 0

        if idx in checkpointed:
            logger.info(
                "Chunk %d already checkpointed for run %s, skipping",
                idx,
                run_id,
            )
            results.append(
                {
                    "chunk_index": idx,
                    "ok": True,
                    "operation": operation,
                    "native_result": None,
                    "skipped": True,
                    "duration_ms": 0,
                    "error": None,
                }
            )
            continue

        try:
            request = build_execute_request(
                plan=plan,
                operation=operation,
                source_config=source_config,
                destination_config=destination_config,
            )
            timeout = int(destination_config.get("chunk_timeout_seconds", _CHUNK_TIMEOUT_SECONDS))

            current_idx = idx

            def make_progress_callback(idx_captured: int, cb: Any) -> Any:
                def wrapped(event: dict[str, Any]) -> None:
                    if cb is not None:
                        event = dict(event)
                        event["chunk_index"] = idx_captured
                        cb(event)

                return wrapped

            chunk_progress = make_progress_callback(current_idx, on_progress)

            native_result: NativeExecuteResult = execute_request(
                request,
                config=destination_config,
                on_progress=chunk_progress,
                timeout_seconds=timeout,
            )
            chunk_duration_ms = int((time.monotonic() - start) * 1000)

            if native_result.ok and native_result.results:
                op_result = native_result.results[0]
                ok = op_result.success
                error: str | None = op_result.error if not ok else None
            else:
                ok = False
                error = native_result.error or "cryo-engine returned no results"
                op_result = None

            if not ok and is_commit_race_lost(error):
                # First-class signal: compaction keeps losing the optimistic-concurrency
                # race against a hot writer. Surface it distinctly rather than as a
                # generic chunk failure so operators/alerts can act (scoping doc §3.2).
                logger.warning(
                    "Native compaction is losing the commit race on table %s "
                    "(chunk %d, run %s): %s",
                    plan.table_ref,
                    idx,
                    run_id,
                    error,
                )

            if ok:
                _write_chunk_checkpoint(
                    session_factory=session_factory,
                    run_id=run_id,
                    chunk_index=idx,
                    operation=operation,
                    op_result=op_result,
                    duration_ms=chunk_duration_ms,
                    native_result=native_result,
                    table_id=table_id,
                )

            results.append(
                {
                    "chunk_index": idx,
                    "ok": ok,
                    "operation": operation,
                    "native_result": op_result,
                    "skipped": False,
                    "duration_ms": chunk_duration_ms,
                    "error": error,
                }
            )

        except Exception as exc:
            chunk_duration_ms = int((time.monotonic() - start) * 1000)
            logger.exception(
                "Chunk %d failed for run %s: %s",
                idx,
                run_id,
                exc,
            )
            results.append(
                {
                    "chunk_index": idx,
                    "ok": False,
                    "operation": operation,
                    "native_result": None,
                    "skipped": False,
                    "duration_ms": chunk_duration_ms,
                    "error": str(exc),
                }
            )

        total_duration_ms += chunk_duration_ms

    return results


def _write_chunk_checkpoint(
    session_factory: Any,
    run_id: str,
    chunk_index: int,
    operation: Operation,
    op_result: NativeOperationResult | None,
    duration_ms: int,
    native_result: NativeExecuteResult,
    *,
    table_id: str | None = None,
) -> None:
    partition_summary = None
    if operation.predicates:
        terms = operation.predicates[0].terms
        if terms:
            first = terms[0]
            partition_summary = f"{first.base_column}[{first.transform.value}]={first.value}"

    with session_factory() as session:
        write_checkpoint(
            session=session,
            run_id=run_id,
            chunk_index=chunk_index,
            operation_type=operation.type.value,
            table_id=table_id,
            partition_summary=partition_summary,
            input_files=op_result.input_files if op_result else None,
            output_files=op_result.output_files if op_result else None,
            bytes_removed=op_result.bytes_removed if op_result else None,
            duration_ms=duration_ms,
            raw_result={
                "table_ref": native_result.table_ref,
                "ok": native_result.ok,
                "stdout": native_result.stdout,
            }
            if native_result
            else None,
        )
        session.commit()
