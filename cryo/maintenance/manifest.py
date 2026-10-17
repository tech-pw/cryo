"""Turn selected tables into ordered Spark SQL, reusing the planner and the Spark renderer.

Nothing here decides *what* maintenance a table gets -- build_execution_plan does, exactly as
it did for per-job runs (partition-aware chunking, delete-compaction policy, protected
snapshots, the shared-location orphan gate, canonical operation order). This module only
collects the rendered statements into the lane manifest the Spark pod executes.
"""

from __future__ import annotations

import gzip
import json
import uuid
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from cryo.domain.enums import Mode
from cryo.engines.spark.sql import render
from cryo.planner.execution import ExecutionDestination, build_execution_plan
from cryo.planner.serialization import plan_to_dict

# Deep mode plus compute_statistics is "every operation the table needs": the planner still
# only emits convert_deletes / rewrite_position_deletes / optimize when the stats call for
# them. Retention matches the most conservative of the retired presets (7 days, keep 4).
DEFAULT_PARAMS: dict[str, Any] = {
    "strategy": "binpack",
    "file_size_mb": 256,
    "chunk_size_gb": 50,
    "max_partitions_per_chunk": 100,
    "min_small_files": 10,
    "min_input_files": 2,
    "partial_progress": True,
    "max_commits": 50,
    "max_concurrent_file_group_rewrites": 5,
    "retention_days": 7,
    "retain_last": 4,
    "orphan_retention_days": 7,
    "compute_statistics": True,
    "stats_compute_ndv": False,
    "delete_ratio_trigger": 0,
    "partition_write_idle_hours": 0,
    # Lanes always run spark.k8s.image (Dockerfile.spark pins Iceberg 1.11), and this
    # rewrite_data_files option needs Iceberg >= 1.7.
    "remove_dangling_deletes": True,
    # Optimize leaves files up to 10x target alone instead of rewriting them only to split
    # them (LinkedIn OpenHouse's setting); see cryo/engines/spark/sql.py::_render_optimize.
    "max_file_size_ratio": 10,
}


@dataclass(frozen=True)
class TableData:
    stats: Mapping[str, Any]
    partitions: Sequence[Mapping[str, Any]]


@dataclass(frozen=True)
class Statement:
    seq: int
    action: str
    sql: str


@dataclass
class TableWork:
    table_id: uuid.UUID
    table_ref: str
    plan: dict[str, Any]
    statements: list[Statement] = field(default_factory=list)


def build_table_work(
    table_id: uuid.UUID,
    *,
    load_table: Callable[[uuid.UUID], TableData],
    destination: ExecutionDestination,
    params: Mapping[str, Any],
    protected_snapshot_ids: Callable[[uuid.UUID], Sequence[int]],
    has_shared_location: Callable[[uuid.UUID], bool],
    excluded_operations: Collection[str] = (),
) -> TableWork | None:
    """None when the planner finds nothing worth running for this table."""
    data = load_table(table_id)
    plan = build_execution_plan(
        stats=data.stats,
        partitions=data.partitions,
        mode=Mode.DEEP,
        params=params,
        destination=destination,
        operations=None,
        protected_snapshot_ids=protected_snapshot_ids(table_id),
        has_shared_location=has_shared_location(table_id),
        excluded_operations=excluded_operations,
    )
    if not plan.operations:
        return None
    statements = [
        Statement(seq=seq, action=rendered.action.value, sql=rendered.sql)
        for seq, rendered in enumerate(render(plan))
    ]
    if not statements:
        return None
    return TableWork(
        table_id=table_id,
        table_ref=plan.table_ref,
        plan=plan_to_dict(plan),
        statements=statements,
    )


def lane_manifest_bytes(
    run_id: uuid.UUID,
    lane_index: int,
    work: Sequence[TableWork],
    *,
    measure_orphan_bytes: bool = False,
) -> bytes:
    payload = {
        "run_id": str(run_id),
        "lane": lane_index,
        "measure_orphan_bytes": measure_orphan_bytes,
        "tables": [
            {
                "table_id": str(item.table_id),
                "table_ref": item.table_ref,
                "statements": [
                    {"seq": s.seq, "action": s.action, "sql": s.sql} for s in item.statements
                ],
            }
            for item in work
        ],
    }
    # mtime=0 keeps the bytes reproducible for the same input.
    return gzip.compress(json.dumps(payload, separators=(",", ":")).encode("utf-8"), mtime=0)
