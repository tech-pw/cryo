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


def render(plan: MaintenancePlan) -> list[RenderedStatement]:
    catalog, table_name = _spark_table(plan.table_ref)
    return [_render_operation(catalog, table_name, operation) for operation in plan.operations]


def render_predicate(
    predicate: PartitionPredicate,
    supported_capabilities: set[PredicateCapability] | None = None,
) -> str:
    capabilities = supported_capabilities or DEFAULT_RENDERABLE_CAPABILITIES
    unsupported_reason = predicate.unsupported_for(capabilities)
    if unsupported_reason:
        raise UnsupportedPredicateError(unsupported_reason)
    return "(" + " AND ".join(_render_term(term) for term in predicate.terms) + ")"


def _render_operation(catalog: str, table_name: str, operation: Operation) -> RenderedStatement:
    if operation.type is OperationType.EXPIRE_SNAPSHOTS:
        sql = _render_expire(catalog, table_name, operation.params)
    elif operation.type is OperationType.REMOVE_ORPHAN_FILES:
        sql = _render_orphan(catalog, table_name, operation.params)
    elif operation.type is OperationType.OPTIMIZE:
        sql = _render_optimize(catalog, table_name, operation)
    elif operation.type is OperationType.CONVERT_DELETES:
        sql = _render_convert_deletes(catalog, table_name, operation.params)
    elif operation.type is OperationType.REWRITE_POSITION_DELETES:
        sql = f"CALL {catalog}.system.rewrite_position_delete_files(table => '{catalog}.{table_name}')"
    elif operation.type is OperationType.OPTIMIZE_MANIFESTS:
        sql = f"CALL {catalog}.system.rewrite_manifests(table => '{catalog}.{table_name}')"
    elif operation.type is OperationType.COMPUTE_STATISTICS:
        sql = _render_compute_statistics(catalog, table_name, operation.params)
    else:
        raise ValueError(f"Unsupported operation: {operation.type}")
    return RenderedStatement(action=operation.type, sql=sql, engine=EngineType.SPARK_EMR)


def _render_expire(catalog: str, table_name: str, params: dict[str, Any]) -> str:
    retain_last = int(params.get("retain_last", 1))
    older_than = _older_than_expr(params)
    protected_ids = sorted({int(s) for s in params.get("protected_snapshot_ids") or []})
    set_clause = ""
    if protected_ids:
        set_clause = (
            f"CALL {catalog}.system.set_snapshot_ref("
            f"table => '{catalog}.{table_name}', "
            f"snapshot_id => {protected_ids[-1]}, "
            f"ref_name => 'cryo-protected-latest'); "
        )
    expire_call = (
        f"CALL {catalog}.system.expire_snapshots("
        f"table => '{catalog}.{table_name}', older_than => {older_than}, retain_last => {retain_last})"
    )
    return f"{set_clause}{expire_call}"


def _render_orphan(catalog: str, table_name: str, params: dict[str, Any]) -> str:
    older_than = _older_than_expr(params)
    return (
        f"CALL {catalog}.system.remove_orphan_files("
        f"table => '{catalog}.{table_name}', older_than => {older_than})"
    )


def _render_optimize(catalog: str, table_name: str, operation: Operation) -> str:
    target_bytes = int(
        operation.params.get("target_file_size_bytes")
        or int(operation.params.get("file_size_mb", 256)) * 1024 * 1024
    )
    # Per-group rewrite floor (Iceberg procedure). Distinct from planner
    # min_small_files (table-level "should we schedule OPTIMIZE?").
    min_input_files = int(operation.params.get("min_input_files", 2))
    args = [f"table => '{catalog}.{table_name}'"]

    # Strategy (binpack, sort)
    strategy = operation.params.get("strategy", "binpack")
    args.append(f"strategy => '{strategy}'")

    # Partition filter
    if operation.predicates:
        where = " OR ".join(render_predicate(predicate) for predicate in operation.predicates)
        args.append(f"where => '{_escape_sql_string(where)}'")

    # Build options map
    options = [
        f"'target-file-size-bytes', '{target_bytes}'",
        f"'min-input-files', '{min_input_files}'",
    ]

    # Sort/Z-order configuration
    sort_columns = operation.params.get("sort_columns", [])
    zorder_columns = operation.params.get("zorder_columns", [])

    if zorder_columns:
        # Z-order clustering
        zorder_expr = ", ".join(f"`{col}`" for col in zorder_columns)
        options.append(f"'sort-order', 'zorder({zorder_expr})'")
    elif sort_columns:
        # Linear sort
        sort_exprs = []
        sort_directions = operation.params.get("sort_directions", ["asc"] * len(sort_columns))
        for col, direction in zip(sort_columns, sort_directions, strict=False):
            sort_exprs.append(f"`{col}` {direction.upper()}")
        options.append(f"'sort-order', '{', '.join(sort_exprs)}'")

    # Additional options
    if operation.params.get("rewrite_all"):
        options.append("'rewrite-all', 'true'")
    if operation.params.get("partial_progress", True):
        options.append("'partial-progress.enabled', 'true'")
    max_concurrent = operation.params.get("max_concurrent_file_group_rewrites", 5)
    options.append(f"'max-concurrent-file-group-rewrites', '{max_concurrent}'")

    args.append(f"options => map({', '.join(options)})")
    return f"CALL {catalog}.system.rewrite_data_files(" + ", ".join(args) + ")"


def _render_convert_deletes(catalog: str, table_name: str, params: dict[str, Any]) -> str:
    threshold = int(params.get("delete_file_threshold", 1))
    partial_progress_enabled = bool(params.get("partial_progress_enabled", True))
    options = [
        f"'delete-file-threshold', '{max(1, threshold)}'",
        f"'partial-progress.enabled', '{str(partial_progress_enabled).lower()}'",
    ]
    return (
        f"CALL {catalog}.system.rewrite_data_files("
        f"table => '{catalog}.{table_name}', options => map({', '.join(options)}))"
    )


def _render_compute_statistics(catalog: str, table_name: str, params: dict[str, Any]) -> str:
    """Render compute_table_stats CALL for Spark (Puffin NDV statistics).

    Spark uses: CALL system.compute_table_stats(table => '...', columns => ARRAY(...))
    This refreshes NDV Theta sketches stored in Puffin files, improving join ordering.

    Bloom filter columns are configured via table properties — rendered separately.
    """
    columns: list[str] = params.get("columns", [])
    bloom_filter_columns: list[str] = params.get("bloom_filter_columns", [])

    statements = []

    # Main stats computation
    if columns:
        cols_expr = ", ".join(f"'{c}'" for c in columns)
        stats_sql = (
            f"CALL {catalog}.system.compute_table_stats("
            f"table => '{catalog}.{table_name}', columns => ARRAY({cols_expr}))"
        )
    else:
        stats_sql = f"CALL {catalog}.system.compute_table_stats(table => '{catalog}.{table_name}')"
    statements.append(stats_sql)

    # Enable Bloom filters for high-cardinality columns via table properties
    for col in bloom_filter_columns:
        statements.append(
            f"ALTER TABLE {catalog}.{table_name} "
            f"SET TBLPROPERTIES ('write.parquet.bloom-filter-enabled.column.{col}' = 'true')"
        )

    # Multiple statements joined — executor must split on ";\n"
    return ";\n".join(statements)


def render_clustering_operation(
    catalog: str,
    table_name: str,
    strategy: str,
    columns: list[str],
    *,
    partition_filter: str | None = None,
    target_file_size_bytes: int = 256 * 1024 * 1024,
    rewrite_all: bool = False,
) -> str:
    """Render a standalone clustering operation (sort or z-order).

    This is used by the layout optimizer to generate SQL for data re-organization.
    """
    args = [f"table => '{catalog}.{table_name}'", f"strategy => '{strategy}'"]

    if partition_filter:
        args.append(f"where => '{_escape_sql_string(partition_filter)}'")

    options = [
        f"'target-file-size-bytes', '{target_file_size_bytes}'",
        "'partial-progress.enabled', 'true'",
        "'max-concurrent-file-group-rewrites', '5'",
    ]

    if rewrite_all:
        options.append("'rewrite-all', 'true'")

    if strategy == "zorder" and columns:
        zorder_expr = ", ".join(f"`{col}`" for col in columns)
        options.append(f"'sort-order', 'zorder({zorder_expr})'")
    elif strategy == "sort" and columns:
        sort_expr = ", ".join(f"`{col}` ASC" for col in columns)
        options.append(f"'sort-order', '{sort_expr}'")

    args.append(f"options => map({', '.join(options)})")
    return f"CALL {catalog}.system.rewrite_data_files(" + ", ".join(args) + ")"


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


def _older_than_expr(params: dict[str, Any]) -> str:
    cutoff = params.get("older_than") or params.get("cutoff_timestamp")
    if cutoff:
        return f"TIMESTAMP '{_timestamp_string(cutoff)}'"
    return f"current_timestamp() - INTERVAL {int(params.get('retention_days', 7))} DAYS"


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


def _spark_table(table_ref: str) -> tuple[str, str]:
    parts = table_ref.split(".")
    if len(parts) >= 3:
        return parts[0], ".".join(parts[1:])
    if len(parts) == 2:
        return "spark_catalog", table_ref
    raise ValueError("table_ref must be schema.table or catalog.schema.table")


def _escape_sql_string(value: str) -> str:
    return value.replace("'", "''")


def _is_numeric_type(col_type: str) -> bool:
    return any(
        token in col_type for token in ("int", "long", "double", "float", "decimal", "numeric")
    )


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) or (isinstance(value, str) and _matches_number(value))


def _looks_like_int(value: str) -> bool:
    return value.removeprefix("-").isdigit()


def _matches_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _quote_identifier(identifier: str) -> str:
    if identifier.replace("_", "").isalnum() and not identifier[0].isdigit():
        return identifier
    return "`" + identifier.replace("`", "``") + "`"
