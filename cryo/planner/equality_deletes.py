"""Select partitions whose equality deletes should become position deletes.

The planner emits no SQL. No current engine can perform the conversion, so
build_plan only emits the operation when a preset opts in. Rendering then
fails closed instead of rewriting data files.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from cryo.domain.enums import OperationType
from cryo.domain.models import Operation


def equality_delete_count(row: Mapping[str, Any]) -> int:
    raw_value = row.get("raw")
    raw: Mapping[str, Any] = raw_value if isinstance(raw_value, Mapping) else {}
    value = row.get("equality_delete_files", raw.get("equality_delete_files", 0))
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def partitions_with_equality_deletes(
    partitions: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    return tuple(row for row in partitions if equality_delete_count(row) > 0)


def plan_convert_equality_deletes(
    stats: Mapping[str, Any],
    partitions: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
) -> tuple[Operation | None, tuple[str, ...]]:
    if not bool(params.get("convert_equality_deletes")):
        return None, ()
    table_count = int(stats.get("equality_delete_files") or 0)
    selected = partitions_with_equality_deletes(partitions)
    if table_count <= 0 and not selected:
        return None, ("Skipped convert_equality_deletes: no equality-delete files.",)
    selected_count = sum(equality_delete_count(row) for row in selected) or table_count
    notes = (
        "Planned convert_equality_deletes for "
        f"{selected_count} equality-delete files. Data files stay untouched; "
        "equality deletes are replaced by position deletes. "
        "No engine implements this yet, so execution fails closed.",
    )
    return (
        Operation(
            OperationType.CONVERT_EQUALITY_DELETES,
            {
                "equality_delete_files": selected_count,
                "partition_count": len(selected),
                "rewrite_data_files": False,
            },
        ),
        notes,
    )
