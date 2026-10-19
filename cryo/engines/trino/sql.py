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
from cryo.engines.expire_data_sql import expire_data_metadata, render_expire_data_delete
from cryo.planner.partitions import DEFAULT_RENDERABLE_CAPABILITIES


class UnsupportedPredicateError(ValueError):
    pass


def session_setup() -> list[str]:
    return [
        "SET SESSION task_min_writer_count = 8",
        "SET SESSION writer_scaling_min_data_processed = '32MB'",
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
    elif operation.type is OperationType.CONVERT_EQUALITY_DELETES:
        raise UnsupportedPredicateError(
            "convert_equality_deletes is not a Trino procedure. "
            "It must not be rewritten as OPTIMIZE, which would rewrite data files."
        )
    elif operation.type is OperationType.CONVERT_DELETES:
        # Trino does not expose Spark's delete-file-threshold options; OPTIMIZE is the closest
        # equivalent and rewrites data files to compact delete fragments.
        sql = _render_optimize(table_ref, operation)
    elif operation.type is OperationType.OPTIMIZE_MANIFESTS:
        sql = f"ALTER TABLE {table_ref} EXECUTE optimize_manifests"
    elif operation.type is OperationType.COMPUTE_STATISTICS:
        sql = _render_compute_statistics(table_ref, operation.params)
    elif operation.type is OperationType.EXPIRE_DATA:
        sql = render_expire_data_delete(table_ref, operation.params, quote="trino")
    elif operation.type is OperationType.REWRITE_POSITION_DELETES:
        # Trino has no ALTER TABLE EXECUTE equivalent of Iceberg's rewrite_position_delete_files
        # procedure (Spark-only as of the connectors this renderer targets). Route this
        # operation to a Spark/native destination instead of silently downgrading it to a full
        # OPTIMIZE, which would defeat the point of the cheaper minor-compaction path.
        raise UnsupportedPredicateError(
            "rewrite_position_delete_files is not supported on Trino destinations; "
            "route this table's minor delete compaction to a Spark or native destination."
        )
    else:
        raise ValueError(f"Unsupported operation: {operation.type}")
    return RenderedStatement(
        action=operation.type,
        sql=sql,
        engine=EngineType.TRINO,
        metadata={
            "table_ref": table_ref,
            **expire_data_metadata(operation.type.value, operation.params),
        },
    )


def _render_expire(table_ref: str, params: dict[str, Any]) -> str:
    retention_days = int(params.get("retention_days", 7))
    retain_last = int(params.get("retain_last", 1))
    # Trino 483 expire_snapshots cannot keep a specific snapshot id, and it has no
    # set_snapshots procedure. Emitting one used to fail the whole expire (and the
    # rest of the plan) whenever a Cryo exclusion row existed.
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
    """Parse a date from ISO text or Iceberg epoch-day (int / digit string).

    Identity-partitioned date columns store raw days-since-1970-01-01 in scan
    stats; day-transform values use the same encoding. Accept both that form and
    ``YYYY-MM-DD`` so predicate rendering does not call ``date.fromisoformat`` on
    values like ``'20643'``.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, int):
        return date(1970, 1, 1) + timedelta(days=value)
    text = str(value).strip()
    if _looks_like_int(text):
        return date(1970, 1, 1) + timedelta(days=int(text))
    return date.fromisoformat(text[:10])


def _date_string(value: Any) -> str:
    return _parse_date(value).isoformat()


def _timestamp_string(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return f"{value.isoformat()} 00:00:00"
    # Epoch-day ints / digit strings (same encoding as identity date partitions).
    if isinstance(value, int) or (isinstance(value, str) and _looks_like_int(value.strip())):
        return f"{_parse_date(value).isoformat()} 00:00:00"
    text = str(value).strip()
    if len(text) == 10:
        return f"{text} 00:00:00"
    return text.replace("T", " ")[:19]


def _is_numeric_type(col_type: str) -> bool:
    return any(
        token in col_type for token in ("int", "long", "double", "float", "decimal", "numeric")
    )


def _render_compute_statistics(table_ref: str, params: dict[str, Any]) -> str:
    """Render ANALYZE TABLE for Trino (computes Puffin NDV statistics).

    Trino uses: ANALYZE table [WITH (columns = ARRAY['col1', 'col2'])]
    This refreshes NDV sketches in Puffin files, improving join ordering.
    """
    columns: list[str] = params.get("columns", [])
    if columns:
        cols_expr = ", ".join(f"'{c}'" for c in columns)
        return f"ANALYZE {table_ref} WITH (columns = ARRAY[{cols_expr}])"
    return f"ANALYZE {table_ref}"


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


def quote_identifier(identifier: str) -> str:
    """Always double-quote a Trino identifier, escaping embedded quotes."""
    return '"' + identifier.replace('"', '""') + '"'


def quote_qualified_name(*parts: str) -> str:
    """Quote each part of a dotted Trino name (catalog.schema.table) independently."""
    return ".".join(_quote_identifier(part) for part in parts)
