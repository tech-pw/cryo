"""Read-through query pattern aggregation from an existing Iceberg-native query log table.

Some Trino deployments already run a query event listener that writes every completed query
into an Iceberg table (rather than relying on Trino's own `system.runtime.queries`, which is
transient/in-memory and bounded by `query.min-expire-age`). When that table already exists,
Cryo doesn't need to poll a live Trino coordinator or keep its own copy of every query in
`query_logs` at all — it can read the existing table directly (with a time-range predicate
pushed down via PyIceberg, not a full scan) and aggregate straight into `query_patterns`.

This is a distinct path from `cryo.telemetry.ingestion.TrinoQueryIngester`, which polls a live
Trino coordinator's HTTP API and persists full rows into `query_logs` -- that path is still the
right default when there's no pre-existing durable query log table. This module is the opt-in
alternative for sources that already have one.

Enable per-Source by setting, in `Source.config`::

    {
      "query_log": {
        "mode": "iceberg_table",
        "table": "logging.trino_query_log",   # namespace.table, read via this source's own catalog
        "query_text_column": "query",          # optional, defaults shown
        "executed_at_column": "create_time",   # optional, defaults shown
        "inputs_json_column": "inputs_json"    # optional, defaults shown; null/false disables
      }
    }

Two columns are required -- the raw SQL text (parsed the same way `cryo/telemetry/parser.py`
parses any other query text, to find filter/join/projection columns and the referenced table)
and a timestamp column (to push down a `since` filter and to persist a per-source watermark so
repeat runs are incremental, not a full re-scan every time). No `query_logs` rows are written
for this path; only `query_patterns` is updated.

A third, optional column (`inputs_json_column`, defaulting to `"inputs_json"` -- the name Trino's
own event listener uses) unlocks real per-query selectivity: Trino's query-completion event
carries, per input table, `physicalInputRows` and (for Iceberg tables) `connectorInfo.totalRecords`
-- the table's total row count at the snapshot that query scanned. `physicalInputRows /
totalRecords` is genuine partition-pruning evidence, attributed to whichever filter columns that
query used, and is what makes `query_patterns.avg_selectivity` (consumed by
`cryo.optimizer.pto.candidate_space.PtoPattern.pruning_value` for layout/partition
recommendations) a real signal instead of always falling back to the "no information" 0.5
default. Set `inputs_json_column` to `null`/`false` for event-listener tables that don't have an
inputs-shaped column -- filter/join/projection column stats are still collected either way.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from cryo.db.models import Source, Table
from cryo.telemetry.parser import extract_table_from_query, parse_query_predicates
from cryo.telemetry.patterns import (
    ColumnPatternStats,
    _upsert_query_patterns,
    record_table_query_activity,
    refresh_query_count_30d,
)

logger = logging.getLogger(__name__)

_DEFAULT_QUERY_TEXT_COLUMN = "query"
_DEFAULT_EXECUTED_AT_COLUMN = "create_time"
# Trino's query-completion event listener (see e.g. trino_logs.trino_queries) writes one JSON
# array per query under this column: one object per input table actually read, each carrying
# physicalInputRows and, when the connector reports it (Iceberg does), connectorInfo.totalRecords
# -- the table's total row count as of the snapshot that query scanned. That pair is real,
# per-query selectivity ground truth (physicalInputRows / totalRecords), not a placeholder.
# Optional: older event-listener table versions or non-Iceberg connectors may not have this
# column or may leave connectorInfo empty, in which case selectivity is simply not recorded for
# those queries rather than the whole read-through path failing.
_DEFAULT_INPUTS_JSON_COLUMN = "inputs_json"
_DEFAULT_LOOKBACK_DAYS = 7  # only used the first time a source has no persisted watermark yet


@dataclass(frozen=True)
class IcebergQueryLogConfig:
    """Parsed `Source.config["query_log"]` block for the iceberg_table mode."""

    table_identifier: str  # "namespace.table"
    query_text_column: str = _DEFAULT_QUERY_TEXT_COLUMN
    executed_at_column: str = _DEFAULT_EXECUTED_AT_COLUMN
    inputs_json_column: str | None = _DEFAULT_INPUTS_JSON_COLUMN
    last_aggregated_at: datetime | None = None

    @property
    def namespace_and_table(self) -> tuple[str, str]:
        if "." not in self.table_identifier:
            raise ValueError(
                f"query_log.table must be 'namespace.table', got {self.table_identifier!r}"
            )
        namespace, table = self.table_identifier.rsplit(".", 1)
        return namespace, table


def parse_iceberg_query_log_config(source_config: dict[str, Any]) -> IcebergQueryLogConfig | None:
    """Return the parsed config if this source is set up for read-through Iceberg ingestion."""
    block = source_config.get("query_log")
    if not isinstance(block, dict) or block.get("mode") != "iceberg_table":
        return None
    table_identifier = block.get("table")
    if not table_identifier:
        logger.warning("query_log.mode is 'iceberg_table' but 'table' is not set; skipping")
        return None

    last_aggregated_at = None
    raw_watermark = block.get("last_aggregated_at")
    if isinstance(raw_watermark, str):
        try:
            last_aggregated_at = datetime.fromisoformat(raw_watermark)
        except ValueError:
            logger.warning("Could not parse query_log.last_aggregated_at=%r", raw_watermark)

    # Explicit false/null disables selectivity extraction for sources whose event-listener
    # table doesn't have an inputs_json-shaped column, without disabling the whole read-through
    # path -- filter/join/projection column stats still have value on their own.
    inputs_json_column: str | None = _DEFAULT_INPUTS_JSON_COLUMN
    if "inputs_json_column" in block:
        raw_col = block.get("inputs_json_column")
        inputs_json_column = str(raw_col) if raw_col else None

    return IcebergQueryLogConfig(
        table_identifier=str(table_identifier),
        query_text_column=str(block.get("query_text_column") or _DEFAULT_QUERY_TEXT_COLUMN),
        executed_at_column=str(block.get("executed_at_column") or _DEFAULT_EXECUTED_AT_COLUMN),
        inputs_json_column=inputs_json_column,
        last_aggregated_at=last_aggregated_at,
    )


def _read_query_texts(
    source: Source,
    config: IcebergQueryLogConfig,
    *,
    since: datetime,
    until: datetime,
    max_rows: int,
) -> tuple[list[tuple[str, str | None]], bool]:
    """Scan the query-log Iceberg table for query text (and, when configured, that query's
    ``inputs_json``) within a bounded half-open time window.

    Reads batch-by-batch (``to_arrow_batch_reader``) rather than materializing the whole window
    with ``to_arrow()``, and stops at ``max_rows`` as a last-resort safety valve. The caller is
    responsible for keeping each window itself small (see ``aggregate_from_iceberg_query_log``)
    -- a busy Trino cluster's query-event-listener table can log millions of rows with large raw
    SQL text per row, and an unbounded scan of that over a multi-day lookback is what previously
    OOM'd the process reading it. Returns ``(rows, truncated)`` where each row is
    ``(query_text, inputs_json_or_None)``; ``truncated`` is True if ``max_rows`` was hit, so the
    caller can log that rows were dropped for this window.
    """
    from pyiceberg.expressions import And, GreaterThan, LessThanOrEqual

    from cryo.sources.factory import source_catalog_for

    namespace, table_name = config.namespace_and_table
    catalog = source_catalog_for(source)
    iceberg_table = catalog.load_table(namespace, table_name)

    selected_fields = (config.query_text_column, config.executed_at_column)
    if config.inputs_json_column:
        selected_fields = (*selected_fields, config.inputs_json_column)

    scan = iceberg_table.scan(
        row_filter=And(
            GreaterThan(config.executed_at_column, since),  # type: ignore[misc,call-arg,arg-type]
            LessThanOrEqual(config.executed_at_column, until),  # type: ignore[misc,call-arg,arg-type]
        ),
        selected_fields=selected_fields,
    )

    rows: list[tuple[str, str | None]] = []
    for batch in scan.to_arrow_batch_reader():
        query_col = batch.column(config.query_text_column).to_pylist()
        inputs_col = (
            batch.column(config.inputs_json_column).to_pylist()
            if config.inputs_json_column
            else [None] * len(query_col)
        )
        for value, inputs_json in zip(query_col, inputs_col, strict=True):
            if value:
                rows.append((str(value), inputs_json))
            if len(rows) >= max_rows:
                return rows, True
    return rows, False


def _selectivity_for_query(
    inputs_json: str | None,
    *,
    schema_name: str,
    table_name: str,
) -> float | None:
    """Extract this query's selectivity against one specific table from its ``inputs_json``.

    ``inputs_json`` is a JSON array with one object per table the query actually read (see the
    module docstring). Matches by ``schema``/``table`` rather than position since a query can
    join several tables and Cryo only wants the selectivity for the table this pattern-scan
    call is attributing columns to. Returns ``None`` -- not 0.0 -- whenever real numbers aren't
    available (missing column, unparseable JSON, connector didn't report totalRecords, or a
    total of 0 which would make the ratio meaningless): ``None`` is the "no information" value
    ``PtoPattern.pruning_value`` already knows how to fall back on, whereas a fabricated 0.0
    would silently claim perfect pruning that was never observed.
    """
    if not inputs_json:
        return None
    try:
        inputs = json.loads(inputs_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(inputs, list):
        return None

    for entry in inputs:
        if not isinstance(entry, dict):
            continue
        if entry.get("schema") != schema_name or entry.get("table") != table_name:
            continue
        physical_input_rows = entry.get("physicalInputRows")
        connector_info = entry.get("connectorInfo")
        if not isinstance(connector_info, dict):
            continue
        total_records = connector_info.get("totalRecords")
        try:
            physical_input_rows = float(physical_input_rows)
            total_records = float(total_records)
        except (TypeError, ValueError):
            return None
        if total_records <= 0:
            return None
        return physical_input_rows / total_records

    return None


def aggregate_from_iceberg_query_log(
    session: Session,
    source: Source,
    *,
    now: datetime | None = None,
) -> int:
    """Read new rows from the source's configured Iceberg query-log table and aggregate them
    straight into query_patterns -- no query_logs rows are written for this source.

    Processes the backlog in fixed time windows (``settings.query_log_iceberg_window_hours``),
    persisting the watermark after each window completes so the range up to that point is never
    silently skipped even if a later window in the same call fails or the per-call window budget
    (``settings.query_log_iceberg_max_windows_per_tick``) runs out -- the rest is picked up on the
    next scheduled tick. This bounds memory to one window's volume instead of the whole lookback
    range (previously up to 7 days, unbounded), which OOM'd on a busy Trino cluster's
    query-event-listener table.

    Returns the number of query_patterns rows updated.
    """
    config = parse_iceberg_query_log_config(source.config or {})
    if config is None:
        return 0

    from cryo.config import get_settings

    settings = get_settings()
    now = now or datetime.now(UTC)
    since = config.last_aggregated_at or (now - timedelta(days=_DEFAULT_LOOKBACK_DAYS))
    window = timedelta(hours=settings.query_log_iceberg_window_hours)

    tables = (
        session.query(Table).filter(Table.source_id == source.id, Table.is_active.is_(True)).all()
    )
    table_lookup: dict[str, Table] = {f"{t.schema_name}.{t.table_name}".lower(): t for t in tables}

    table_stats: defaultdict[uuid.UUID, dict[str, ColumnPatternStats]] = defaultdict(dict)
    all_query_counts: defaultdict[uuid.UUID, int] = defaultdict(int)
    total_read = 0
    total_matched = 0
    windows_processed = 0
    cursor = since

    while cursor < now and windows_processed < settings.query_log_iceberg_max_windows_per_tick:
        window_end = min(cursor + window, now)
        try:
            query_texts, truncated = _read_query_texts(
                source,
                config,
                since=cursor,
                until=window_end,
                max_rows=settings.query_log_iceberg_max_rows_per_window,
            )
        except Exception as exc:
            logger.warning(
                "Failed to read Iceberg query log table %r for source %s, window [%s, %s]: %s",
                config.table_identifier,
                source.id,
                cursor,
                window_end,
                exc,
            )
            break

        if truncated:
            logger.warning(
                "Iceberg query log window [%s, %s] for source %s exceeded %s rows; excess rows "
                "in this window were dropped",
                cursor,
                window_end,
                source.id,
                settings.query_log_iceberg_max_rows_per_window,
            )

        total_read += len(query_texts)
        # Per-window, not per-call: a single call can cover several windows (multi-day backlog
        # catch-up on a source's first run), and query_texts don't carry per-row timestamps
        # (_read_query_texts only projects the query-text column) -- bucketing every matched
        # query in this window under window_end's own date is close enough for a 30-day rolling
        # count and needs no extra column read.
        window_query_counts: defaultdict[uuid.UUID, int] = defaultdict(int)
        for query_text, inputs_json in query_texts:
            schema_name, table_name = extract_table_from_query(query_text)
            if not schema_name or not table_name:
                continue
            table = table_lookup.get(f"{schema_name}.{table_name}".lower())
            if table is None:
                continue
            total_matched += 1
            window_query_counts[table.id] += 1
            predicate_info = parse_query_predicates(query_text)
            selectivity = _selectivity_for_query(
                inputs_json, schema_name=schema_name, table_name=table_name
            )
            _accumulate(table_stats[table.id], predicate_info, selectivity=selectivity)

        if window_query_counts:
            record_table_query_activity(session, window_query_counts, day=window_end.date())
            for tid, count in window_query_counts.items():
                all_query_counts[tid] += count

        # This window is fully read and accumulated -- safe to advance the watermark past it
        # even if a later window in this call fails or the windows-per-tick budget runs out.
        _persist_watermark(session, source, window_end)
        cursor = window_end
        windows_processed += 1

    updated = _upsert_query_patterns(session, table_stats)
    if all_query_counts:
        refresh_query_count_30d(session, set(all_query_counts.keys()), now=now)
    logger.info(
        "Iceberg query log read-through: %s window(s), %s query rows read, %s matched to known "
        "tables, %s patterns updated, watermark now %s",
        windows_processed,
        total_read,
        total_matched,
        updated,
        cursor,
    )
    return updated


def _accumulate(
    stats_by_col: dict[str, ColumnPatternStats],
    predicate_info: Any,
    *,
    selectivity: float | None = None,
) -> None:
    """Fold one query's predicate/join/projection columns into the running per-table stats.

    ``selectivity`` (fraction of the table's rows this specific query actually read, from
    ``_selectivity_for_query``) is attributed only to this query's *filter* columns -- it's the
    pruning outcome of whatever predicate the query applied, so it belongs on the columns that
    predicate touched, not on unrelated join or pure-projection columns the query also
    happened to reference.
    """
    for col in predicate_info.filter_columns:
        if not col.column:
            continue
        stats = stats_by_col.setdefault(col.column, ColumnPatternStats(column_name=col.column))
        stats.filter_count += 1
        if selectivity is not None:
            stats.record_selectivity_sample(selectivity)
        if col.is_equality:
            stats.equality_filter_count += 1
        if col.is_range:
            stats.range_filter_count += 1

    for col in predicate_info.join_columns:
        if not col:
            continue
        stats_by_col.setdefault(col, ColumnPatternStats(column_name=col)).join_count += 1

    for col in predicate_info.projection_columns:
        if not col:
            continue
        stats_by_col.setdefault(col, ColumnPatternStats(column_name=col)).projection_count += 1


def _persist_watermark(session: Session, source: Source, now: datetime) -> None:
    config = dict(source.config or {})
    query_log_block = dict(config.get("query_log") or {})
    query_log_block["last_aggregated_at"] = now.isoformat()
    config["query_log"] = query_log_block
    source.config = config
    session.add(source)
    session.commit()
