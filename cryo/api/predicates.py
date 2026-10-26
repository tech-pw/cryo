from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, and_, false, func, not_, or_, select, true

from cryo.api.table_maintenance import COMPUTED_FIELDS
from cryo.db.models import Source, Table, TableAssignment, TableCost, TableStats
from cryo.domain.predicates import (
    LEAF_OPERATORS,
    LIKE_ESCAPE,
    SUPPORTED_FIELDS,
    _glob_to_like,
    evaluate_predicate,
)

__all__ = ["compile_predicate", "evaluate_predicate"]

FIELD_MAP = {
    "source_id": Table.source_id,
    "schema_name": Table.schema_name,
    "table_name": Table.table_name,
    "is_active": Table.is_active,
    "consecutive_scan_failures": Table.consecutive_scan_failures,
    "last_scan_error_kind": Table.last_scan_error_kind,
    "last_snapshot_committed_at": TableStats.last_snapshot_committed_at,
    "needs_compaction": TableStats.needs_compaction,
    "compaction_priority": TableStats.compaction_priority,
    "grade": TableStats.grade,
    "overall_score": TableStats.overall_score,
    "total_files": TableStats.total_files,
    "small_files_count": TableStats.small_files_count,
    "total_partitions": TableStats.total_partitions,
    "total_size_gb": TableStats.total_size_gb,
    "total_snapshots": TableStats.total_snapshots,
    "manifest_count": TableStats.manifest_count,
    "partition_col": TableStats.partition_col,
    "partition_col_type": TableStats.partition_col_type,
    "query_count_30d": TableStats.query_count_30d,
    "projected_savings_usd_month": TableStats.projected_savings_usd_month,
    "projected_monthly_s3_cost_usd": TableStats.projected_monthly_s3_cost_usd,
    "reclaimable_bytes": TableStats.reclaimable_bytes,
    "estimated_monthly_s3_cost_usd": TableStats.estimated_monthly_s3_cost_usd,
    "cost_precision": TableCost.precision,
    "assigned_preset_id": TableAssignment.preset_id,
    "scan_at": TableStats.scan_at,
}

# Explorer-only fields: not in SUPPORTED_FIELDS, so preset rules (evaluated in Python against
# candidate rows that carry none of these) can't reference them.
_TIMESTAMP_FIELDS = frozenset({"last_snapshot_committed_at", "scan_at"})
EXPLORER_FIELDS = frozenset({"scan_at", "last_scan_error_kind", *COMPUTED_FIELDS})


def compile_predicate(predicate: Mapping[str, Any] | None) -> ColumnElement[bool] | None:
    if not predicate:
        return None
    if "all" in predicate:
        clauses = [_required_compile(item) for item in _children(predicate["all"])]
        return and_(*clauses) if clauses else true()
    if "any" in predicate:
        clauses = [_required_compile(item) for item in _children(predicate["any"])]
        return or_(*clauses) if clauses else false()
    if "not" in predicate:
        return not_(_required_compile(_child(predicate["not"])))
    return _compile_leaf(predicate)


def _required_compile(predicate: Mapping[str, Any]) -> ColumnElement[bool]:
    clause = compile_predicate(predicate)
    if clause is None:
        raise ValueError("empty predicate is not allowed inside compound predicates")
    return clause


def _compile_leaf(predicate: Mapping[str, Any]) -> ColumnElement[bool]:
    field_name = _field(predicate)
    op = _operator(predicate)
    value = predicate.get("value")
    if field_name in _TIMESTAMP_FIELDS and isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    computed = COMPUTED_FIELDS.get(field_name)
    column = computed() if computed is not None else FIELD_MAP.get(field_name)
    if column is None:
        raise ValueError(f"Unsupported filter field: {field_name}")

    if op == "=":
        return column == value
    if op == "!=":
        return column != value
    if op == "<":
        return column < value
    if op == "<=":
        return column <= value
    if op == ">":
        return column > value
    if op == ">=":
        return column >= value
    if op == "in":
        if not isinstance(value, list | tuple | set):
            raise ValueError("in predicate value must be a collection")
        if field_name == "source_id":
            return _source_id_in(value)
        return column.in_(value)
    if op in {"like", "glob"} and field_name in {"schema_name", "table_name"}:
        pattern = _glob_to_like(str(value)) if op == "glob" else str(value)
        return column.ilike(pattern, escape=LIKE_ESCAPE)
    if op == "like":
        return column.like(str(value), escape=LIKE_ESCAPE)
    if op == "glob":
        return column.like(_glob_to_like(str(value)), escape=LIKE_ESCAPE)

    raise ValueError(f"Unsupported filter operator: {op}")


def _source_id_in(value: list | tuple | set) -> ColumnElement[bool]:
    ids: list[UUID] = []
    names: list[str] = []
    for item in value:
        text = str(item)
        try:
            ids.append(UUID(text))
        except ValueError:
            names.append(text)
    clauses: list[ColumnElement[bool]] = []
    if ids:
        clauses.append(Table.source_id.in_(ids))
    if names:
        lowered = [name.lower() for name in names]
        clauses.append(
            Table.source_id.in_(select(Source.id).where(func.lower(Source.name).in_(lowered)))
        )
    return or_(*clauses) if clauses else false()


def _field(predicate: Mapping[str, Any]) -> str:
    field_name = predicate.get("field")
    if not isinstance(field_name, str):
        raise ValueError("leaf predicate must include a string field")
    if field_name not in SUPPORTED_FIELDS and field_name not in EXPLORER_FIELDS:
        raise ValueError(f"Unsupported filter field: {field_name}")
    return field_name


def _operator(predicate: Mapping[str, Any]) -> str:
    op = str(predicate.get("op") or "=").lower()
    if op not in LEAF_OPERATORS:
        raise ValueError(f"Unsupported filter operator: {op}")
    return op


def _children(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("compound predicate children must be a list")
    if not all(isinstance(child, Mapping) for child in value):
        raise ValueError("compound predicate children must be objects")
    return value


def _child(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("not predicate must be an object")
    return value
