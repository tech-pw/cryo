from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
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
    rendered = (_render_operation(catalog, table_name, operation) for operation in plan.operations)
    return [statement for statement in rendered if statement is not None]


def render_predicate(
    predicate: PartitionPredicate,
    supported_capabilities: set[PredicateCapability] | None = None,
) -> str:
    capabilities = supported_capabilities or DEFAULT_RENDERABLE_CAPABILITIES
    unsupported_reason = predicate.unsupported_for(capabilities)
    if unsupported_reason:
        raise UnsupportedPredicateError(unsupported_reason)
    return "(" + " AND ".join(_render_term(term) for term in predicate.terms) + ")"


def _render_operation(
    catalog: str, table_name: str, operation: Operation
) -> RenderedStatement | None:
    if operation.type is OperationType.EXPIRE_SNAPSHOTS:
        sql = _render_expire(catalog, table_name, operation.params)
    elif operation.type is OperationType.REMOVE_ORPHAN_FILES:
        sql = _render_orphan(catalog, table_name, operation.params)
    elif operation.type is OperationType.OPTIMIZE:
        sql = _render_optimize(catalog, table_name, operation)
    elif operation.type is OperationType.CONVERT_DELETES:
        sql = _render_convert_deletes(catalog, table_name, operation)
    elif operation.type is OperationType.REWRITE_POSITION_DELETES:
        sql = _render_rewrite_position_deletes(catalog, table_name, operation)
    elif operation.type is OperationType.OPTIMIZE_MANIFESTS:
        sql = f"CALL {catalog}.system.rewrite_manifests(table => '{catalog}.{table_name}')"
    elif operation.type is OperationType.COMPUTE_STATISTICS:
        stats_sql = _render_compute_statistics(catalog, table_name, operation.params)
        if stats_sql is None:
            return None
        sql = stats_sql
    else:
        raise ValueError(f"Unsupported operation: {operation.type}")
    return RenderedStatement(
        action=operation.type, sql=sql, engine=EngineType.SPARK_EMR, metadata={"catalog": catalog}
    )


def _render_expire(catalog: str, table_name: str, params: dict[str, Any]) -> str:
    retain_last = int(params.get("retain_last", 1))
    older_than = _older_than_expr(params)
    protected_ids = sorted({int(s) for s in params.get("protected_snapshot_ids") or []})
    tag_stmt = ""
    if protected_ids:
        # expire_snapshots never removes a snapshot a tag points at. Snapshot ids are
        # random, so tagging only the largest id kept the wrong commit. One tag per id.
        tag_stmt = "".join(
            f"ALTER TABLE {catalog}.{table_name} CREATE OR REPLACE TAG "
            f"`cryo-protect-{snapshot_id}` AS OF VERSION {snapshot_id};\n"
            for snapshot_id in protected_ids
        )
    expire_call = (
        f"CALL {catalog}.system.expire_snapshots("
        f"table => '{catalog}.{table_name}', older_than => {older_than}, "
        f"retain_last => {retain_last}"
    )
    if params.get("stream_results", True):
        # Iceberg's docs recommend this to keep the driver from collecting every expired
        # file path at once; lanes run Spark in local mode, where driver memory is the pod.
        expire_call += ", stream_results => true"
    expire_call += ")"
    return f"{tag_stmt}{expire_call}"


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
    min_file_size_bytes = _min_file_size_bytes(operation.params, target_bytes)
    if strategy == "binpack" and min_file_size_bytes is not None:
        # Iceberg's default treats every file under 75% of the target (192 MB at 256 MB) as a
        # rewrite candidate, while the planner picks tables and partitions by files under the
        # scanner's small-file threshold (89.6 MB). The gap is what made a table with 11 small
        # files rewrite 206 files / 23 GB in 40 minutes on 2026-09-24. Pinning the procedure's
        # threshold to the planner's keeps the rewrite to the files that were counted.
        options.append(f"'min-file-size-bytes', '{min_file_size_bytes}'")
    max_file_size_bytes = _max_file_size_bytes(operation.params, target_bytes)
    if strategy == "binpack" and max_file_size_bytes is not None:
        # Files above max-file-size-bytes (default 180% of target) are rewritten on their own
        # just to split them, which buys a reader nothing: a large file is still read in
        # split-sized pieces. LinkedIn OpenHouse sets 10x target for the same reason. The read
        # splits stay target-sized, so output files still land near the target.
        options.append(f"'max-file-size-bytes', '{max_file_size_bytes}'")

    # Sort/Z-order configuration
    sort_columns = operation.params.get("sort_columns", [])
    zorder_columns = operation.params.get("zorder_columns", [])
    write_ordered_by_stmt: str | None = None

    if zorder_columns:
        # Z-order/Hilbert clustering -- Iceberg's rewrite_data_files procedure takes this
        # inline via the options map, no table-level sort-order metadata needed first.
        # ``curve`` is resolved upstream by cryo.optimizer.curve_capability, which downgrades
        # an unsupported Hilbert request to zorder rather than letting the procedure fail at
        # parse time on an older Iceberg/Spark; anything unrecognised here falls back to
        # zorder for the same reason.
        curve = str(operation.params.get("curve", "zorder")).lower()
        if curve not in ("zorder", "hilbert"):
            curve = "zorder"
        zorder_expr = ", ".join(f"`{col}`" for col in zorder_columns)
        options.append(f"'sort-order', '{curve}({zorder_expr})'")
    elif sort_columns:
        # Linear sort. Regression, 2026-08-11: rewrite_data_files(strategy => 'sort') does
        # NOT read a linear sort order from the options map the way it does for zorder --
        # confirmed live on lsq_pw.silver_prospect_activity_base, which failed with
        # "Cannot sort data without a valid sort order, table ... is unsorted and no sort
        # order is provided" even though 'sort-order' was set in options. Iceberg's rewrite
        # procedure only honors a linear sort strategy against the table's own persisted
        # sort-order metadata (see docs/native-iceberg-go-compaction-plan.md's "table
        # sort-order metadata on write" framing) -- so it must be set via ALTER TABLE ...
        # WRITE ORDERED BY *before* this rewrite call, not passed inline and not persisted
        # only after (which is what execute_layout_recommendation used to do).
        sort_exprs = []
        sort_directions = operation.params.get("sort_directions", ["asc"] * len(sort_columns))
        for col, direction in zip(sort_columns, sort_directions, strict=False):
            sort_exprs.append(f"`{col}` {direction.upper()}")
        order_expr = ", ".join(sort_exprs)
        write_ordered_by_stmt = f"ALTER TABLE {catalog}.{table_name} WRITE ORDERED BY {order_expr}"

    # Additional options
    if operation.params.get("rewrite_all"):
        options.append("'rewrite-all', 'true'")
    if operation.params.get("partial_progress", True):
        options.append("'partial-progress.enabled', 'true'")
        # max-commits sets commit granularity, not how much gets rewritten: Iceberg commits
        # ceil(file groups / max-commits) groups at a time and rewrites every planned group
        # regardless (RewriteDataFilesSparkAction.doExecuteWithPartialProgress). A higher
        # value persists progress in smaller steps and shrinks what one commit conflict
        # throws away, at the cost of more snapshots for expire_snapshots to clean up.
        max_commits = operation.params.get("max_commits", 50)
        options.append(f"'partial-progress.max-commits', '{max_commits}'")
    max_concurrent = operation.params.get("max_concurrent_file_group_rewrites", 5)
    options.append(f"'max-concurrent-file-group-rewrites', '{max_concurrent}'")
    options.append(f"'rewrite-job-order', '{_rewrite_job_order(operation.params)}'")

    args.append(f"options => map({', '.join(options)})")
    rewrite_call = f"CALL {catalog}.system.rewrite_data_files(" + ", ".join(args) + ")"
    if write_ordered_by_stmt is not None:
        rewrite_call = f"{write_ordered_by_stmt};\n{rewrite_call}"

    # target-file-size-bytes above only governs this one rewrite -- without also persisting
    # it (and a sensible write.distribution-mode) as table properties, the next writer that
    # isn't Cryo (a different Spark job, Flink, whatever) has no idea what target this table
    # was tuned to and can reintroduce the exact fragmentation optimize just fixed. hash
    # distribution groups same-partition rows together during writes, Iceberg's own general
    # guidance for partitioned tables; it's a no-op for unpartitioned ones, not harmful.
    if not operation.params.get("persist_target_file_size", True):
        return rewrite_call
    properties_stmt = (
        f"ALTER TABLE {catalog}.{table_name} SET TBLPROPERTIES ("
        f"'write.target-file-size-bytes' = '{target_bytes}', "
        "'write.distribution-mode' = 'hash')"
    )
    return f"{properties_stmt};\n{rewrite_call}"


def _render_convert_deletes(catalog: str, table_name: str, operation: Operation) -> str:
    params = operation.params
    threshold = int(params.get("delete_file_threshold", 1))
    partial_progress_enabled = bool(params.get("partial_progress_enabled", True))
    args = [f"table => '{catalog}.{table_name}'"]

    # Partition filter -- without this, rewrite_data_files self-selects file groups across
    # the WHOLE table using its own binpack/size heuristics, which can silently starve
    # heavily-skewed partitions of attention run after run. Confirmed live, 2026-07-22: a
    # table with 200 partitions averaging 19 files each, but 10+ partitions holding 700-791
    # files apiece, had a global convert_deletes call touch the exact same 46 files and
    # remove zero deletes across three separate attempts (including one with 4x the Spark
    # executors) -- the skewed partitions were never reached. Scoping the call to the
    # worst partitions directly (planner-attached via operation.predicates, same mechanism
    # OPTIMIZE already uses) forces the rewrite to actually cover them instead of leaving it
    # to chance.
    if operation.predicates:
        where = " OR ".join(render_predicate(predicate) for predicate in operation.predicates)
        args.append(f"where => '{_escape_sql_string(where)}'")

    options = [
        f"'delete-file-threshold', '{max(1, threshold)}'",
        f"'partial-progress.enabled', '{str(partial_progress_enabled).lower()}'",
    ]
    if partial_progress_enabled:
        # Commit granularity and group parallelism, both overridable. max-commits does not cap
        # how much is rewritten (see _render_optimize); the 2026-07-22 "46 of 13.6k files"
        # pass was the unscoped where-clause above, not these values.
        max_commits = int(params.get("convert_deletes_max_commits", 50))
        max_concurrent_rewrites = int(params.get("convert_deletes_max_concurrent_rewrites", 5))
        options.append(f"'partial-progress.max-commits', '{max(1, max_commits)}'")
        options.append(f"'max-concurrent-file-group-rewrites', '{max(1, max_concurrent_rewrites)}'")
    if params.get("remove_dangling_deletes", False):
        # Rewriting a data file applies its deletes but leaves v2 position/equality delete
        # files tracked as "dangling" until something removes them. All 59 convert_deletes
        # statements of the 2026-09-24 nightly run reported removed_delete_files_count=0.
        # This adds one commit (only when something is dangling) that drops delete files
        # older than every live data file in their partition. Iceberg >= 1.7 only: older
        # runtimes reject the option, so callers opt in (the batch path does).
        options.append("'remove-dangling-deletes', 'true'")
    options.append(f"'rewrite-job-order', '{_rewrite_job_order(params)}'")
    args.append(f"options => map({', '.join(options)})")
    return f"CALL {catalog}.system.rewrite_data_files(" + ", ".join(args) + ")"


def _render_rewrite_position_deletes(catalog: str, table_name: str, operation: Operation) -> str:
    """Minor delete compaction: merge small position-delete files, drop dangling rows.

    Iceberg sizes each group's Spark tasks by bytes (FILE_OPEN_COST=0), so a partition of
    thousands of tiny delete files is one or two tasks reading file after file. Partial
    progress keeps finished groups when a later one fails, and files-desc starts with the
    groups holding the most delete files.
    """
    params = operation.params
    options: list[str] = []
    if params.get("partial_progress_enabled", True):
        max_commits = int(params.get("rewrite_position_deletes_max_commits", 10))
        options += [
            "'partial-progress.enabled', 'true'",
            f"'partial-progress.max-commits', '{max(1, max_commits)}'",
        ]
    max_concurrent = int(params.get("rewrite_position_deletes_max_concurrent_rewrites", 5))
    options.append(f"'max-concurrent-file-group-rewrites', '{max(1, max_concurrent)}'")
    options.append(f"'rewrite-job-order', '{_rewrite_job_order(params)}'")
    return (
        f"CALL {catalog}.system.rewrite_position_delete_files("
        f"table => '{catalog}.{table_name}', options => map({', '.join(options)}))"
    )


def _render_compute_statistics(catalog: str, table_name: str, params: dict[str, Any]) -> str | None:
    """Render compute_table_stats CALL for Spark (Puffin NDV statistics).

    Spark uses: CALL system.compute_table_stats(table => '...', columns => ARRAY(...))
    This refreshes NDV Theta sketches stored in Puffin files, improving join ordering.
    It is a full scan of every selected column (Iceberg's NDVSketchUtil aggregates a theta
    sketch over the whole snapshot), so it is only rendered when ``compute_ndv`` is on.
    None when there is nothing to do (NDV off and no bloom-filter columns).

    Bloom filter columns are configured via table properties — rendered separately.
    """
    columns: list[str] = params.get("columns") or []
    bloom_filter_columns: list[str] = params.get("bloom_filter_columns") or []
    compute_ndv = params.get("compute_ndv", True) is not False

    statements = []

    if compute_ndv:
        if columns:
            cols_expr = ", ".join(f"'{c}'" for c in columns)
            stats_sql = (
                f"CALL {catalog}.system.compute_table_stats("
                f"table => '{catalog}.{table_name}', columns => ARRAY({cols_expr}))"
            )
        else:
            stats_sql = (
                f"CALL {catalog}.system.compute_table_stats(table => '{catalog}.{table_name}')"
            )
        statements.append(stats_sql)

    # Enable Bloom filters for high-cardinality columns via table properties
    for col in bloom_filter_columns:
        statements.append(
            f"ALTER TABLE {catalog}.{table_name} "
            f"SET TBLPROPERTIES ('write.parquet.bloom-filter-enabled.column.{col}' = 'true')"
        )

    if not statements:
        return None
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
    # Iceberg's rewrite_data_files accepts only 'binpack' and 'sort' as strategy values.
    # 'zorder' is accepted historically as a strategy alias, but 'hilbert' is not: Iceberg
    # 1.12.0 expresses Hilbert purely through the sort-order option under strategy => 'sort'.
    # Sending strategy => 'hilbert' fails at procedure-parse time.
    procedure_strategy = "sort" if strategy == "hilbert" else strategy
    args = [f"table => '{catalog}.{table_name}'", f"strategy => '{procedure_strategy}'"]

    if partition_filter:
        args.append(f"where => '{_escape_sql_string(partition_filter)}'")

    options = [
        f"'target-file-size-bytes', '{target_file_size_bytes}'",
        "'partial-progress.enabled', 'true'",
        "'max-concurrent-file-group-rewrites', '5'",
    ]

    if rewrite_all:
        options.append("'rewrite-all', 'true'")

    if strategy in ("zorder", "hilbert") and columns:
        # Both curves share rewrite_data_files' 'sort-order' option; only the function name
        # differs. Callers must resolve Hilbert support first (curve_capability) -- Iceberg
        # only gained hilbert() in 1.12.0 on Spark 4.1.
        curve_expr = ", ".join(f"`{col}`" for col in columns)
        options.append(f"'sort-order', '{strategy}({curve_expr})'")
    elif strategy == "sort" and columns:
        sort_expr = ", ".join(f"`{col}` ASC" for col in columns)
        options.append(f"'sort-order', '{sort_expr}'")

    args.append(f"options => map({', '.join(options)})")
    return f"CALL {catalog}.system.rewrite_data_files(" + ", ".join(args) + ")"


def render_wair_recluster(
    catalog: str,
    table_name: str,
    column: str,
    boundary_values: Sequence[Any],
    *,
    column_type: str,
    target_file_size_bytes: int = 256 * 1024 * 1024,
) -> str:
    """Sort-rewrite only the files that straddle queried range endpoints (WAIR boundary files).

    ``where`` selects every file whose min/max may contain one of the endpoint values -- exactly
    the files partially overlapping those query boundaries. ``sort_order`` is passed as the
    procedure argument, so the table's own write order is left untouched.
    """
    if not boundary_values:
        raise ValueError("WAIR recluster needs at least one boundary value")
    quoted = _quote_identifier(column)
    where = " OR ".join(f"{quoted} = {_literal(v, column_type)}" for v in boundary_values)
    options = [
        "'rewrite-all', 'true'",
        f"'target-file-size-bytes', '{target_file_size_bytes}'",
        "'partial-progress.enabled', 'true'",
        "'max-concurrent-file-group-rewrites', '5'",
    ]
    args = [
        f"table => '{catalog}.{table_name}'",
        "strategy => 'sort'",
        f"sort_order => '{_escape_sql_string(quoted)} ASC NULLS LAST'",
        f"where => '{_escape_sql_string(where)}'",
        f"options => map({', '.join(options)})",
    ]
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
    # A literal, not `current_timestamp() - INTERVAL N DAYS` -- Spark 4.1.2's stricter SQL
    # parameter binder rejects that expression form for CALL procedure args with
    # "IllegalArgumentException: number of args and params must match after binding"
    # (confirmed live, 2026-07-24). Computing the cutoff here sidesteps it entirely.
    retention_days = int(params.get("retention_days", 7))
    computed_cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    return f"TIMESTAMP '{_timestamp_string(computed_cutoff)}'"


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


_REWRITE_JOB_ORDERS = frozenset({"bytes-asc", "bytes-desc", "files-asc", "files-desc", "none"})


def _rewrite_job_order(params: dict[str, Any]) -> str:
    # files-desc commits the groups that remove the most files first, so a rewrite cut short
    # (deadline, lane death, a failed group) has already banked the biggest reductions.
    order = str(params.get("rewrite_job_order") or "files-desc").lower()
    return order if order in _REWRITE_JOB_ORDERS else "files-desc"


def _min_file_size_bytes(params: dict[str, Any], target_bytes: int) -> int | None:
    raw_bytes = params.get("min_file_size_bytes")
    raw_mb = params.get("min_file_size_mb")
    try:
        if raw_bytes is not None:
            value = int(raw_bytes)
        elif raw_mb is not None:
            value = int(float(raw_mb) * 1024 * 1024)
        else:
            return None
    except (TypeError, ValueError):
        return None
    # Iceberg rejects min-file-size-bytes >= target-file-size-bytes at procedure time.
    if value <= 0 or value >= target_bytes:
        return None
    return value


def _max_file_size_bytes(params: dict[str, Any], target_bytes: int) -> int | None:
    raw = params.get("max_file_size_ratio")
    if raw is None:
        return None
    try:
        ratio = float(raw)
    except (TypeError, ValueError):
        return None
    # Iceberg requires max-file-size-bytes > target-file-size-bytes; at or under its own
    # 1.8x default this would only make splitting more aggressive.
    if ratio <= 1.8:
        return None
    return int(target_bytes * ratio)


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
