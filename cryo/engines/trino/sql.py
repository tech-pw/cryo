from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from cryo.domain.enums import EngineType, OperationType, PartitionTransform, PredicateCapability
from cryo.domain.models import (
    MaintenancePlan,
    Operation,
    PartitionPredicate,
    PartitionPredicateTerm,
)
from cryo.engines.base import RenderedStatement
from cryo.planner.partitions import DEFAULT_RENDERABLE_CAPABILITIES


class UnsupportedPredicateError(ValueError):
    pass


def session_setup(catalog: str = "iceberg", target_file_size_mb: int = 256) -> list[str]:
    return [
        f"SET SESSION {catalog}.target_max_file_size='{target_file_size_mb}MB'",
        "SET SESSION task_min_writer_count=8",
        "SET SESSION writer_scaling_min_data_processed='32MB'",
    ]


def render(plan: MaintenancePlan) -> list[RenderedStatement]:
    return [_render_operation(plan.table_ref, operation) for operation in plan.operations]


def render_predicate(
    predicate: PartitionPredicate,
    supported_capabilities: set[PredicateCapability] | None = None,
) -> str:
    capabilities = supported_capabilities or DEFAULT_RENDERABLE_CAPABILITIES
    unsupported_reason = predicate.unsupported_for(capabilities)
    if unsupported_reason:
        raise UnsupportedPredicateError(unsupported_reason)
    return "(" + " AND ".join(_render_term(term) for term in predicate.terms) + ")"


def _render_operation(table_ref: str, operation: Operation) -> RenderedStatement:
    if operation.type is OperationType.EXPIRE_SNAPSHOTS:
        sql = _render_expire(table_ref, operation.params)
    elif operation.type is OperationType.REMOVE_ORPHAN_FILES:
        sql = _render_orphan(table_ref, operation.params)
    elif operation.type is OperationType.OPTIMIZE:
        sql = _render_optimize(table_ref, operation)
    elif operation.type is OperationType.OPTIMIZE_MANIFESTS:
        sql = f"ALTER TABLE {table_ref} EXECUTE optimize_manifests"
    else:
        raise ValueError(f"Unsupported operation: {operation.type}")
    return RenderedStatement(action=operation.type, sql=sql, engine=EngineType.TRINO)


def _render_expire(table_ref: str, params: dict[str, Any]) -> str:
    retention_days = int(params.get("retention_days", 7))
    retain_last = int(params.get("retain_last", 1))
    return (
        f"ALTER TABLE {table_ref} "
        "EXECUTE expire_snapshots("
        f"retention_threshold => '{retention_days}d', retain_last => {retain_last})"
    )


def _render_orphan(table_ref: str, params: dict[str, Any]) -> str:
    retention_days = int(params.get("retention_days", 7))
    return (
        f"ALTER TABLE {table_ref} "
        f"EXECUTE remove_orphan_files(retention_threshold => '{retention_days}d')"
    )


def _render_optimize(table_ref: str, operation: Operation) -> str:
    file_size_mb = int(operation.params.get("file_size_mb", 256))
    sql = f"ALTER TABLE {table_ref} EXECUTE optimize(file_size_threshold => '{file_size_mb}MB')"
    if operation.predicates:
        filters = " OR ".join(render_predicate(predicate) for predicate in operation.predicates)
        sql = f"{sql} WHERE {filters}"
    return sql


def _render_term(term: PartitionPredicateTerm) -> str:
    column = _quote_identifier(term.base_column)
    if term.transform is PartitionTransform.IDENTITY:
        if term.value is None:
            return f"{column} IS NULL"
        return f"{column} = {_literal(term.value, term.col_type)}"
    if term.transform is PartitionTransform.DAY:
        start = _day_start(term.value)
        end = start + timedelta(days=1)
        return _range_condition(column, start, end, term.col_type)
    if term.transform is PartitionTransform.MONTH:
        start = _month_start(term.value)
        end = _add_month(start)
        return _range_condition(column, start, end, term.col_type)
    if term.transform is PartitionTransform.YEAR:
        start = _year_start(term.value)
        end = date(start.year + 1, 1, 1)
        return _range_condition(column, start, end, term.col_type)
    raise UnsupportedPredicateError(f"unsupported partition transform: {term.transform.value}")


def _range_condition(column: str, start: date, end: date, col_type: str) -> str:
    start_literal = _temporal_literal(start, col_type)
    end_literal = _temporal_literal(end, col_type)
    return f"{column} >= {start_literal} AND {column} < {end_literal}"


def _literal(value: Any, col_type: str) -> str:
    lowered = col_type.lower()
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if _is_numeric_type(lowered) and _is_number(value):
        return str(value)
    if "timestamp" in lowered:
        return f"TIMESTAMP '{_timestamp_string(value)}'"
    if lowered == "date" or lowered.startswith("date"):
        return f"DATE '{_date_string(value)}'"
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def _temporal_literal(value: date, col_type: str) -> str:
    if "timestamp" in col_type.lower():
        return f"TIMESTAMP '{value.isoformat()} 00:00:00'"
    return f"DATE '{value.isoformat()}'"


def _day_start(value: Any) -> date:
    if isinstance(value, int):
        return date(1970, 1, 1) + timedelta(days=value)
    if isinstance(value, str) and _looks_like_int(value):
        return date(1970, 1, 1) + timedelta(days=int(value))
    return _parse_date(value)


def _month_start(value: Any) -> date:
    if isinstance(value, int) or (
        isinstance(value, str) and _looks_like_int(value) and len(value) < 4
    ):
        months = int(value)
        return date(1970 + months // 12, months % 12 + 1, 1)
    parsed = _parse_date(f"{value}-01" if isinstance(value, str) and len(value) == 7 else value)
    return date(parsed.year, parsed.month, 1)


def _year_start(value: Any) -> date:
    if isinstance(value, int) or (
        isinstance(value, str) and _looks_like_int(value) and len(value) < 4
    ):
        return date(1970 + int(value), 1, 1)
    return date(int(str(value)[:4]), 1, 1)


def _add_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _date_string(value: Any) -> str:
    return _parse_date(value).isoformat()


def _timestamp_string(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value)
    if len(text) == 10:
        return f"{text} 00:00:00"
    return text.replace("T", " ")[:19]


def _is_numeric_type(col_type: str) -> bool:
    return any(
        token in col_type for token in ("int", "long", "double", "float", "decimal", "numeric")
    )


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) or (isinstance(value, str) and re_match_number(value))


def _looks_like_int(value: str) -> bool:
    return value.removeprefix("-").isdigit()


def re_match_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _quote_identifier(identifier: str) -> str:
    if identifier.replace("_", "").isalnum() and not identifier[0].isdigit():
        return identifier
    return '"' + identifier.replace('"', '""') + '"'
