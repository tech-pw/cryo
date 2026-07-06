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
        "executed_at_column": "create_time"    # optional, defaults shown
      }
    }

Only two columns are actually required -- the raw SQL text (parsed the same way
`cryo/telemetry/parser.py` parses any other query text, to find filter/join/projection columns
and the referenced table) and a timestamp column (to push down a `since` filter and to persist a
per-source watermark so repeat runs are incremental, not a full re-scan every time). No
`query_logs` rows are written for this path; only `query_patterns` is updated.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from cryo.db.models import Source, Table
from cryo.telemetry.parser import extract_table_from_query, parse_query_predicates
from cryo.telemetry.patterns import ColumnPatternStats, _upsert_query_patterns

logger = logging.getLogger(__name__)

_DEFAULT_QUERY_TEXT_COLUMN = "query"
_DEFAULT_EXECUTED_AT_COLUMN = "create_time"
_DEFAULT_LOOKBACK_DAYS = 7  # only used the first time a source has no persisted watermark yet


@dataclass(frozen=True)
class IcebergQueryLogConfig:
    """Parsed `Source.config["query_log"]` block for the iceberg_table mode."""

    table_identifier: str  # "namespace.table"
    query_text_column: str = _DEFAULT_QUERY_TEXT_COLUMN
    executed_at_column: str = _DEFAULT_EXECUTED_AT_COLUMN
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

    return IcebergQueryLogConfig(
        table_identifier=str(table_identifier),
        query_text_column=str(block.get("query_text_column") or _DEFAULT_QUERY_TEXT_COLUMN),
        executed_at_column=str(block.get("executed_at_column") or _DEFAULT_EXECUTED_AT_COLUMN),
        last_aggregated_at=last_aggregated_at,
    )


def _read_query_texts(
    source: Source,
    config: IcebergQueryLogConfig,
    *,
    since: datetime,
) -> list[str]:
    """Scan the query-log Iceberg table for query text, pushing the time filter down."""
    from pyiceberg.expressions import GreaterThan

    from cryo.sources.factory import source_catalog_for

    namespace, table_name = config.namespace_and_table
    catalog = source_catalog_for(source)
    iceberg_table = catalog.load_table(namespace, table_name)

    scan = iceberg_table.scan(
        row_filter=GreaterThan(config.executed_at_column, since),  # type: ignore[misc,call-arg,arg-type]
        selected_fields=(config.query_text_column, config.executed_at_column),
    )
    arrow_table = scan.to_arrow()
    column = arrow_table.column(config.query_text_column)
    return [str(value) for value in column.to_pylist() if value]


def aggregate_from_iceberg_query_log(
    session: Session,
    source: Source,
    *,
    now: datetime | None = None,
) -> int:
    """Read new rows from the source's configured Iceberg query-log table and aggregate them
    straight into query_patterns -- no query_logs rows are written for this source.

    Returns the number of query_patterns rows updated. Advances and persists a watermark on
    `Source.config["query_log"]["last_aggregated_at"]` so repeat calls only scan new rows.
    """
    config = parse_iceberg_query_log_config(source.config or {})
    if config is None:
        return 0

    now = now or datetime.now(UTC)
    since = config.last_aggregated_at or (now - timedelta(days=_DEFAULT_LOOKBACK_DAYS))

    try:
        query_texts = _read_query_texts(source, config, since=since)
    except Exception as exc:
        logger.warning(
            "Failed to read Iceberg query log table %r for source %s: %s",
            config.table_identifier,
            source.id,
            exc,
        )
        return 0

    if not query_texts:
        _persist_watermark(session, source, now)
        return 0

    tables = (
        session.query(Table).filter(Table.source_id == source.id, Table.is_active.is_(True)).all()
    )
    table_lookup: dict[str, Table] = {f"{t.schema_name}.{t.table_name}".lower(): t for t in tables}

    table_stats: defaultdict[uuid.UUID, dict[str, ColumnPatternStats]] = defaultdict(dict)
    matched = 0
    for query_text in query_texts:
        schema_name, table_name = extract_table_from_query(query_text)
        if not schema_name or not table_name:
            continue
        table = table_lookup.get(f"{schema_name}.{table_name}".lower())
        if table is None:
            continue
        matched += 1
        predicate_info = parse_query_predicates(query_text)
        _accumulate(table_stats[table.id], predicate_info)

    updated = _upsert_query_patterns(session, table_stats)
    _persist_watermark(session, source, now)
    logger.info(
        "Iceberg query log read-through: %s query rows read, %s matched to known tables, "
        "%s patterns updated",
        len(query_texts),
        matched,
        updated,
    )
    return updated


def _accumulate(stats_by_col: dict[str, ColumnPatternStats], predicate_info: Any) -> None:
    for col in predicate_info.filter_columns:
        if not col.column:
            continue
        stats = stats_by_col.setdefault(col.column, ColumnPatternStats(column_name=col.column))
        stats.filter_count += 1
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
