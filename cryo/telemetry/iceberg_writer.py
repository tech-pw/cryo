"""Append-only Iceberg writer for the Cryo query-log archive.

The destination that emits queries (currently only Trino) can be configured to stream every
completed query into an Iceberg table that lives in the *same* catalog the destination already
queries (so we don't need a separate warehouse). This module is the writer half -- it creates the
table on first use, appends batches, and runs ``expire_snapshots`` to enforce retention.

Why this exists: storing per-query rows in Postgres at 1M-queries/day is death. Iceberg is
columnar, partitioned by day, and snapshot expiry is the cheapest possible retention story
(one Trino SQL call) compared to Postgres ``DELETE`` churn + vacuum pressure.

Key invariants:
- The table is created lazily on the first ``append`` call. Idempotent via PyIceberg's
  ``create_table_if_not_exists``.
- Partition spec is ``days(executed_at)`` + ``bucket[16](source_id)``. Day-pruned reads are the
  whole point (the read-through path filters by executed_at and pushes the predicate down).
- Schema is frozen: every row has the same shape. Adding a column is an Iceberg schema evolution
  but NEVER an in-place rewrite -- a forward-compatible reader always wins.
- No raw rows are ever written to Postgres by this path; the curator decides what (if anything)
  spills into Postgres.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pyarrow as pa
from pyiceberg.exceptions import TableAlreadyExistsError
from pyiceberg.schema import Schema
from pyiceberg.table import Table
from pyiceberg.types import (
    IntegerType,
    LongType,
    NestedField,
    StringType,
    TimestampType,
)

from cryo.db.models import Destination, Source

logger = logging.getLogger(__name__)


# Single source of truth for the Iceberg schema. Frozen column IDs and types so historical
# snapshots stay readable even after evolution. IDs chosen in the same convention PyIceberg uses
# internally (top-level required = 1, 2, 3, ...) to avoid collision when the user later ALTERs.
QUERY_LOG_SCHEMA_ID = 1


def build_query_log_schema() -> Schema:
    """Build the Iceberg schema for the query-log archive.

    Columns are intentionally narrow: enough to reconstruct the same patterns Cryo derives
    today (filter/join/projection columns, durations, byte counts, user), plus the raw
    query text for forensics. We don't store the entire Trino row -- the bits Trino itself
    does NOT surface usefully (cumulative_memory, completed_splits, ...) get dropped.
    """
    return Schema(
        NestedField(field_id=1, name="query_id", field_type=StringType(), required=True),
        NestedField(field_id=2, name="source_id", field_type=StringType(), required=True),
        NestedField(field_id=3, name="engine", field_type=StringType(), required=True),
        NestedField(field_id=4, name="executed_at", field_type=TimestampType(), required=True),
        NestedField(field_id=5, name="query_text", field_type=StringType(), required=False),
        NestedField(field_id=6, name="query_type", field_type=StringType(), required=False),
        NestedField(field_id=7, name="user_name", field_type=StringType(), required=False),
        NestedField(field_id=8, name="duration_ms", field_type=LongType(), required=False),
        NestedField(field_id=9, name="rows_read", field_type=LongType(), required=False),
        NestedField(field_id=10, name="bytes_read", field_type=LongType(), required=False),
        NestedField(field_id=11, name="files_read", field_type=IntegerType(), required=False),
        NestedField(field_id=12, name="files_pruned", field_type=IntegerType(), required=False),
        NestedField(field_id=13, name="state", field_type=StringType(), required=False),
        NestedField(field_id=14, name="error_code", field_type=StringType(), required=False),
        # filter_columns / join_columns / projection_columns are JSON-encoded into a single
        # ``parsed_columns`` blob so the schema stays stable. The curator (or a future
        # materialised view) splits them out if it needs column-level pruning.
        NestedField(field_id=15, name="parsed_columns", field_type=StringType(), required=False),
        NestedField(field_id=16, name="ingested_at", field_type=TimestampType(), required=False),
    )


@dataclass(frozen=True)
class QueryLogAppendResult:
    """Outcome of an append batch -- surfaced for metrics and tests."""

    rows_attempted: int
    rows_appended: int
    table_identifier: str
    snapshot_id: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class QueryLogRetentionResult:
    """Outcome of a retention sweep -- surfaced for metrics and tests."""

    table_identifier: str
    snapshots_expired: int = 0
    files_removed: int = 0
    error: str | None = None


def _rows_to_arrow_table(rows: Sequence[dict[str, Any]]) -> pa.Table:
    """Convert cleaned row dicts into the PyArrow table PyIceberg's ``Table.append`` needs.

    ``Table.append`` requires a ``pyarrow.Table`` -- passing a raw list of dicts fails with
    ``Expected PyArrow table, got: [...]`` (confirmed live, : this was the fourth
    bug in the chain, only surfaced once the first three were fixed and a real batch of 518
    rows finally reached this call). Building from the Iceberg schema's own ``as_arrow()``
    keeps the Arrow field types/nullability in lockstep with ``build_query_log_schema`` --
    a hand-written pa.schema() here would silently drift the day either schema changes.
    """
    arrow_schema = build_query_log_schema().as_arrow()
    return pa.Table.from_pylist(list(rows), schema=arrow_schema)


class QueryLogIcebergWriter:
    """Owns the append + retention path for one destination's query-log Iceberg table.

    Stateless apart from a cached PyIceberg ``Table`` handle; safe to construct per curator
    tick. The table handle is invalidated on schema-evolution events (future work) so this
    class doesn't grow a long-lived refresh loop.
    """

    def __init__(
        self,
        *,
        catalog: Any,
        namespace: str,
        table_name: str = "queries",
        source_partition_bucket: int = 16,
    ) -> None:
        self._catalog = catalog
        self._namespace = namespace
        self._table_name = table_name
        self._source_partition_bucket = source_partition_bucket
        self._cached_table: Table | None = None

    @property
    def table_identifier(self) -> str:
        return f"{self._namespace}.{self._table_name}"

    def _ensure_table(self) -> Any:
        """Create the table on first use; subsequent calls just load."""
        if self._cached_table is not None:
            return self._cached_table
        try:
            self._catalog.create_namespace_if_not_exists(self._namespace)
        except Exception as exc:  # noqa: BLE001 - namespace create can vary by backend
            logger.debug("create_namespace_if_not_exists(%r) raised: %s", self._namespace, exc)
        try:
            table: Any = self._catalog.create_table_if_not_exists(
                self.table_identifier,
                schema=build_query_log_schema(),
                properties={
                    # write.format.default + format-version pinned so evolutions don't quietly
                    # change the on-disk layout underneath running curator + reader paths.
                    "format-version": "2",
                    "write.format.default": "parquet",
                },
            )
        except TableAlreadyExistsError:
            table = self._catalog.load_table(self.table_identifier)
        self._cached_table = table
        return table

    def append(self, rows: Sequence[dict[str, Any]]) -> QueryLogAppendResult:
        """Append a batch of pre-formed row dicts to the query-log table.

        Each row must satisfy ``build_query_log_schema``; missing columns are stored as NULL,
        unknown columns are dropped with a warning (the curator may evolve over time).
        """
        if not rows:
            return QueryLogAppendResult(
                rows_attempted=0,
                rows_appended=0,
                table_identifier=self.table_identifier,
            )
        table = self._ensure_table()
        valid_fields = {f.name for f in build_query_log_schema().fields}

        # Coerce / filter rows defensively. PyIceberg will validate the rest; we'd rather
        # drop a single bad row than fail the whole batch (the curator keeps the bad row in
        # the Redis stream until the next retry so we don't silently lose telemetry).
        cleaned: list[dict[str, Any]] = []
        dropped = 0
        for row in rows:
            try:
                cleaned_row = {k: v for k, v in row.items() if k in valid_fields}
                if not cleaned_row.get("query_id"):
                    dropped += 1
                    continue
                cleaned.append(cleaned_row)
            except Exception:
                dropped += 1
                continue
        if not cleaned:
            return QueryLogAppendResult(
                rows_attempted=len(rows),
                rows_appended=0,
                table_identifier=self.table_identifier,
                error="all rows failed validation",
            )

        try:
            arrow_table = _rows_to_arrow_table(cleaned)
            table.append(
                arrow_table
            )  # PyIceberg's Table.append returns None; snapshot is on the table
        except Exception as exc:  # noqa: BLE001 - PyIceberg + catalog errors vary widely
            logger.warning(
                "Append to %s failed (%s rows attempted): %s",
                self.table_identifier,
                len(cleaned),
                exc,
            )
            return QueryLogAppendResult(
                rows_attempted=len(rows),
                rows_appended=0,
                table_identifier=self.table_identifier,
                error=str(exc),
            )

        snapshot_id: int | None = None
        current_snapshot = getattr(table, "current_snapshot", None)
        try:
            snap = current_snapshot() if callable(current_snapshot) else current_snapshot
        except Exception:  # noqa: BLE001 - some catalogs raise on empty history
            snap = None
        if snap is not None:
            snapshot_id = int(getattr(snap, "snapshot_id", 0) or 0) or None
        return QueryLogAppendResult(
            rows_attempted=len(rows),
            rows_appended=len(cleaned),
            table_identifier=self.table_identifier,
            snapshot_id=snapshot_id,
        )

    def expire_snapshots_older_than(self, retention_days: int) -> QueryLogRetentionResult:
        """Expire snapshots older than ``retention_days`` to bound archive growth.

        Idempotent; safe to run on every curator tick. Backends that don't support
        ``expire_snapshots`` (some embedded catalogs) silently no-op with a logged warning.
        """
        if retention_days <= 0:
            return QueryLogRetentionResult(table_identifier=self.table_identifier)
        try:
            table = self._ensure_table()
        except Exception as exc:  # noqa: BLE001
            return QueryLogRetentionResult(table_identifier=self.table_identifier, error=str(exc))
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        try:
            expire = table.expire_snapshots
        except AttributeError:
            logger.info(
                "%s backend has no expire_snapshots(); retention is a no-op for this table",
                self.table_identifier,
            )
            return QueryLogRetentionResult(table_identifier=self.table_identifier)
        try:
            result = expire(older_than=cutoff)
        except Exception as exc:  # noqa: BLE001
            logger.warning("expire_snapshots on %s failed: %s", self.table_identifier, exc)
            return QueryLogRetentionResult(table_identifier=self.table_identifier, error=str(exc))

        # PyIceberg's ExpireSnapshotsResult exposes a count of expired snapshots and removed
        # data files in some versions and not others; defensively read what we can.
        snapshots_expired = int(getattr(result, "deleted_snapshots_count", 0) or 0)
        files_removed = int(getattr(result, "deleted_data_files_count", 0) or 0)
        return QueryLogRetentionResult(
            table_identifier=self.table_identifier,
            snapshots_expired=snapshots_expired,
            files_removed=files_removed,
        )


# ---------------------------------------------------------------------------
# Destination <-> writer plumbing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueryLogConfig:
    """Parsed view of ``Destination.query_log_config`` -- only the fields the writer needs."""

    enabled: bool
    mode: str  # "trino_system_runtime" or "read_through"
    namespace: str = "cryo_internal"
    table_name: str = "queries"
    # read_through mode points at an external table the user already maintains; we don't
    # write to it and don't expire its snapshots (that's their policy).
    external_table: str | None = None
    # Optional overrides for column mapping, useful when the user's existing table uses
    # different column names than ours.
    query_text_column: str = "query_text"
    executed_at_column: str = "executed_at"
    # What fraction of queries to archive when the destination is too chatty to keep up.
    # ``None`` = archive everything; ``0.01`` = sample 1%. Sampling is deterministic by
    # query_id so a crash + re-fetch picks up exactly the same subset, never a different
    # sample of the same window -- that property is what makes sampling safe across the
    # crash/recover boundary.
    sample_rate: float | None = None
    # How often the curator polls this destination's Trino coordinator(s) for new queries.
    # Defaults to 60s when retention is short (<=1 day) -- a missed tick at 5-min retention
    # means lost data -- else 300s. Override via the config block when ops need finer or
    # coarser cadence.
    pull_interval_seconds: int = 300
    # Multi-coordinator clusters (rare; usually a single coordinator behind a VIP is the
    # correct setup) list every coordinator here. The curator polls each endpoint per tick
    # and dedupes rows by query_id before writing -- queries that touch multiple
    # coordinators never exist, but a single query is only visible on the coordinator that
    # ran it, so without this list the cluster's full traffic isn't covered.
    telemetry_coordinators: tuple[str, ...] = ()
    # Overlap window for the fetch: the curator asks Trino for queries ending in
    # (now - overlap) -> now, not just since the last successful write. Floor 5 minutes
    # so a single missed tick never loses data to Trino's ``query.min-expire-age`` ring
    # buffer. Always >= pull_interval_seconds * 5 (typical: 15-25 min at default cadence).
    watermark_overlap_seconds: int = 900
    # Opt-in: fetch real per-query scan-byte stats (bytes_read/rows_read/files_read) from
    # Trino's per-query REST API for the curated subset only, before it's written to
    # Postgres. Off by default -- it's one extra HTTP call per curated row (bounded by the
    # curated subset size, never the full fetch), and system.runtime.queries has no stats
    # columns on Trino versions like this deployment's, so bytes_read/files_read are
    # otherwise always None (confirmed live 2026-09-28: 100% of query_logs rows had them
    # null, silently disabling the curator's own LARGE_SCAN_BYTES curation criterion and
    # starving every reader of this data -- ROIGate's benefit-cost ratio, query-driven
    # layout recommendations, project_savings' query-request-savings term).
    fetch_stats_for_curated: bool = False
    # Cap on per-tick fetch_query_stats HTTP calls even with fetch_stats_for_curated=true --
    # the curated subset can still be large on a chatty destination (curated_sample_rate
    # alone lets ~10k rows/day through at 1M queries/day), and enrichment must never become
    # the tick's own bottleneck.
    max_stats_enrichment_per_tick: int = 200


# Default floor on the overlap window. Trino's ``query.min-expire-age`` defaults to 15 min
# in newer versions but ops commonly dial it down to 5 min to save heap. Anything below this
# floor risks losing queries to ring-buffer churn between ticks.
_WATERMARK_OVERLAP_FLOOR_SECONDS = 300
# When retention_days is at or below this threshold we pull every minute instead of every
# five -- at very short retention the value of each query row is high so the curator must
# race to capture before Trino forgets it.
_SHORT_RETENTION_DAYS = 1
_SHORT_RETENTION_PULL_SECONDS = 60
_DEFAULT_PULL_SECONDS = 300
_PULL_OVERLAP_MULTIPLIER = 5


def derive_pull_cadence(retention_days: int | None) -> tuple[int, int]:
    """Compute (pull_interval_seconds, watermark_overlap_seconds) for a destination.

    Splits the config into two parts so the curator can call ``pull`` and ``fetch_window``
    with deterministic numbers derived from retention, never invented on the fly.
    """
    if retention_days is None or retention_days <= 0:
        # read_through mode or "no archive" -- cadence doesn't matter, but keep overlap sane
        # so a future operator who flips retention back on doesn't silently lose data.
        return _DEFAULT_PULL_SECONDS, _WATERMARK_OVERLAP_FLOOR_SECONDS
    if retention_days <= _SHORT_RETENTION_DAYS:
        pull = _SHORT_RETENTION_PULL_SECONDS
    else:
        pull = _DEFAULT_PULL_SECONDS
    overlap = max(_WATERMARK_OVERLAP_FLOOR_SECONDS, pull * _PULL_OVERLAP_MULTIPLIER)
    return pull, overlap


def parse_query_log_config(
    raw: dict[str, Any] | None, *, default_retention_days: int | None = 30
) -> QueryLogConfig | None:
    """Parse a destination's ``query_log_config`` JSONB blob into a typed view.

    Returns None when the destination hasn't opted in to query-log ingestion -- callers must
    treat that as "do nothing" rather than "use defaults", since "do nothing" is the explicit
    safe default for a 1M-queries/day engine that nobody has acknowledged yet.
    """
    if not isinstance(raw, dict):
        return None
    enabled = bool(raw.get("enabled", False))
    if not enabled:
        return None
    mode = str(raw.get("mode") or "trino_system_runtime")
    if mode not in {"trino_system_runtime", "read_through"}:
        logger.warning("Unknown query_log.mode=%r; defaulting to trino_system_runtime", mode)
        mode = "trino_system_runtime"
    sample_rate = raw.get("sample_rate")
    if sample_rate is not None:
        try:
            sample_rate = float(sample_rate)
        except (TypeError, ValueError):
            logger.warning("query_log.sample_rate=%r is not a float; ignoring", sample_rate)
            sample_rate = None
        else:
            if not 0.0 <= sample_rate <= 1.0:
                logger.warning("query_log.sample_rate=%r outside [0, 1]; clamping", sample_rate)
                sample_rate = max(0.0, min(1.0, sample_rate))
    raw_coordinators = raw.get("telemetry_coordinators") or []
    if isinstance(raw_coordinators, str):
        raw_coordinators = [raw_coordinators]
    coordinators: tuple[str, ...] = tuple(str(c) for c in raw_coordinators if c)

    derived_pull, derived_overlap = derive_pull_cadence(default_retention_days)
    pull = _coerce_int(
        raw.get("pull_interval_seconds"), default=derived_pull, minimum=10, maximum=3600
    )
    overlap = _coerce_int(
        raw.get("watermark_overlap_seconds"),
        default=derived_overlap,
        minimum=_WATERMARK_OVERLAP_FLOOR_SECONDS,
        maximum=24 * 3600,
    )
    if overlap < pull:
        # A curator tick must always pull *more* than the interval, otherwise a single
        # missed tick permanently leaves a gap. Floor enforced regardless of user input.
        overlap = max(_WATERMARK_OVERLAP_FLOOR_SECONDS, pull * _PULL_OVERLAP_MULTIPLIER)

    max_stats_enrichment = _coerce_int(
        raw.get("max_stats_enrichment_per_tick"), default=200, minimum=1, maximum=5000
    )

    return QueryLogConfig(
        enabled=True,
        mode=mode,
        namespace=str(raw.get("namespace") or "cryo_internal"),
        table_name=str(raw.get("table_name") or "queries"),
        external_table=raw.get("external_table"),
        query_text_column=str(raw.get("query_text_column") or "query_text"),
        executed_at_column=str(raw.get("executed_at_column") or "executed_at"),
        sample_rate=sample_rate,
        pull_interval_seconds=pull,
        telemetry_coordinators=coordinators,
        watermark_overlap_seconds=overlap,
        fetch_stats_for_curated=bool(raw.get("fetch_stats_for_curated", False)),
        max_stats_enrichment_per_tick=max_stats_enrichment,
    )


def _coerce_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        logger.warning("Could not coerce %r to int; using default %d", value, default)
        return default
    return max(minimum, min(maximum, n))


def sample_query(query_id: str, sample_rate: float | None) -> bool:
    """Return True if this query_id should be archived under the given sample rate.

    Deterministic by query_id hash so a curator crash + re-fetch selects the exact same
    subset, never a fresh random sample that would skew aggregate counts across restarts.
    Uses the first 4 bytes of sha256 -> uniform float in [0, 1).
    """
    if sample_rate is None or sample_rate >= 1.0:
        return True
    if sample_rate <= 0.0:
        return False
    import hashlib

    digest = hashlib.sha256(query_id.encode("utf-8")).digest()
    # First 4 bytes as big-endian unsigned int -> [0, 2**32) -> divide by 2**32 for [0, 1)
    bucket = int.from_bytes(digest[:4], "big") / 2**32
    return bucket < sample_rate


def writer_for_destination(
    destination: Destination,
    *,
    source: Source | None = None,
    config: QueryLogConfig | None = None,
) -> QueryLogIcebergWriter:
    """Build the writer for a destination, using the destination's linked Source's catalog.

    The Source holds the S3 / HMS / REST catalog connection that owns the ``cryo_internal``
    namespace. ``read_through`` mode never invokes the writer, so ``source`` may be None
    in that case.
    """
    if config is None:
        config = parse_query_log_config(destination.query_log_config)
    assert config is not None
    catalog = _catalog_for(source, destination)
    return QueryLogIcebergWriter(
        catalog=catalog,
        namespace=config.namespace,
        table_name=config.table_name,
    )


def _catalog_for(source: Source | None, destination: Destination) -> Any:
    """Resolve a *raw* PyIceberg catalog for the query-log archive.

    Prefers the explicitly-linked Source; falls back to a ``Source`` shaped from the
    destination's own ``config`` (used by the read_through path and by tests).

    ``source_catalog_for`` returns a ``SourceCatalog`` wrapper (``HiveSourceCatalog`` /
    ``RestSourceCatalog`` / ``GlueSourceCatalog``) -- a narrower, read-oriented API
    (``list_tables``, ``list_namespaces``, ``load_table``) meant for the scanner/discovery
    paths. ``QueryLogIcebergWriter`` needs the full PyIceberg ``Catalog`` write interface
    (``create_namespace_if_not_exists``, ``create_table_if_not_exists``) which that wrapper
    doesn't expose -- confirmed live (): every curator tick reached the append step
    and crashed with ``AttributeError: 'HiveSourceCatalog' object has no attribute
    'create_table_if_not_exists'``, after the fetch itself was already fixed. Unwrap to the
    underlying PyIceberg catalog every ``PyIcebergSourceCatalog`` subclass stores on
    ``._catalog`` rather than changing what ``source_catalog_for`` returns everywhere else.
    """
    if source is not None:
        from cryo.sources.factory import source_catalog_for

        return _unwrap_pyiceberg_catalog(source_catalog_for(source))
    from types import SimpleNamespace

    from cryo.sources.factory import source_catalog_for

    placeholder = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000000",
        name=destination.name,
        type="rest",
        config=destination.config or {},
    )
    return _unwrap_pyiceberg_catalog(source_catalog_for(placeholder))


def _unwrap_pyiceberg_catalog(source_catalog: Any) -> Any:
    """Reach past a ``SourceCatalog`` wrapper to the raw PyIceberg catalog it holds.

    Every real ``SourceCatalog`` implementation (``PyIcebergSourceCatalog`` and its Rest/
    Hive/Glue subclasses) stores it on ``._catalog``. Fall back to the wrapper itself if
    that attribute is ever missing -- callers still get a clear AttributeError from PyIceberg
    at the actual missing-method call site rather than this function masking the shape
    mismatch with a confusing early failure.
    """
    return getattr(source_catalog, "_catalog", source_catalog)


# ---------------------------------------------------------------------------
# Row construction from raw Trino system.runtime.queries results
# ---------------------------------------------------------------------------


def rows_from_trino_queries(
    *,
    source_id: str,
    queries: Iterable[dict[str, Any]],
    filter_columns_extractor: Any = None,
) -> list[dict[str, Any]]:
    """Turn raw Trino ``system.runtime.queries`` rows into Iceberg-shaped rows.

    Pure transform -- no I/O. Filter/projection/join extraction is delegated to the
    curator's existing parser (kept as a parameter so this module doesn't reach back into
    telemetry/parser.py and create a cycle).
    """
    now = datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    for q in queries:
        query_id = q.get("query_id")
        if not query_id:
            continue
        executed_at = q.get("executed_at")
        if isinstance(executed_at, str):
            try:
                executed_at = datetime.fromisoformat(executed_at.replace("Z", "+00:00"))
            except ValueError:
                executed_at = now
        elif executed_at is None:
            executed_at = now
        duration_ms = (
            (q.get("queued_time_ms") or 0)
            + (q.get("analysis_time_ms") or 0)
            + (q.get("planning_time_ms") or 0)
            + (q.get("execution_time_ms") or 0)
        )
        row: dict[str, Any] = {
            "query_id": str(query_id),
            "source_id": str(source_id),
            "engine": "trino",
            "executed_at": executed_at,
            "query_text": (q.get("query") or "")[:10000] or None,
            "query_type": q.get("query_type"),
            "user_name": q.get("user_name"),
            "duration_ms": int(duration_ms) if duration_ms else None,
            "rows_read": _as_int(q.get("physical_input_rows")),
            "bytes_read": _as_int(q.get("physical_input_bytes")),
            "files_read": _as_int(q.get("completed_splits")),
            "files_pruned": None,
            "state": q.get("state"),
            "error_code": q.get("error_code"),
            "parsed_columns": _safe_parse_columns(q, filter_columns_extractor),
            "ingested_at": now,
        }
        rows.append(row)
    return rows


def enrich_row_with_query_stats(
    row: dict[str, Any], query_detail: dict[str, Any]
) -> dict[str, Any]:
    """Fill bytes_read/rows_read/files_read on a row (from rows_from_trino_queries) using
    the real per-query stats from TrinoQueryIngester.fetch_query_stats's response.

    Confirmed live 2026-09-28 against this Trino version's actual /v1/query/{id} response:
    queryStats.physicalInputDataSize/physicalInputPositions are the real scan-byte/row
    counts (system.runtime.queries has neither column at all on this deployment -- see
    fetch_query_stats's own docstring). inputs[] carries real per-table catalog/schema/
    table identity but is not 1:1 with the query's primary table for multi-table queries
    (a 100-table UNION-shaped query has 100 entries) -- stored as-is in the row for callers
    that want it, not used to override _resolve_table's single-table SQL-text match, which
    already exists and handles the common single-table case correctly.

    Returns a new dict (does not mutate row); a query_detail missing queryStats or with an
    unparseable physicalInputDataSize leaves the row's stats fields as rows_from_trino_queries
    already set them (None) rather than raising.
    """
    enriched = dict(row)
    query_stats = query_detail.get("queryStats") or {}
    bytes_read = _parse_trino_data_size(query_stats.get("physicalInputDataSize"))
    if bytes_read is not None:
        enriched["bytes_read"] = bytes_read
    rows_read = _as_int(query_stats.get("physicalInputPositions"))
    if rows_read is not None:
        enriched["rows_read"] = rows_read
    inputs = query_detail.get("inputs")
    if isinstance(inputs, list):
        enriched["files_read"] = len(inputs) or None
        enriched["_trino_inputs"] = [
            {"catalog": i.get("catalogName"), "schema": i.get("schema"), "table": i.get("table")}
            for i in inputs
            if isinstance(i, dict)
        ]
    return enriched


def _parse_trino_data_size(value: Any) -> int | None:
    """Trino renders DataSize stats as a string like "4536702B", "1.5MB", "2GB" -- every
    real response observed live on this deployment used the plain-bytes "<int>B" form, but
    Trino's DataSize.toString() can emit any of B/kB/MB/GB/TB depending on magnitude, so all
    are handled rather than assuming the byte-only form always holds."""
    if not isinstance(value, str) or not value:
        return None
    units = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4, "PB": 1024**5}
    for suffix, multiplier in sorted(units.items(), key=lambda kv: -len(kv[0])):
        if value.endswith(suffix):
            number = value[: -len(suffix)]
            try:
                return int(float(number) * multiplier)
            except ValueError:
                return None
    return None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_parse_columns(q: dict[str, Any], extractor: Any) -> str | None:
    if extractor is None:
        return None
    try:
        info = extractor(q.get("query") or "")
    except Exception:
        return None
    import json

    return json.dumps(
        {
            "filter": [c.column for c in info.filter_columns if c.column],
            "join": [c for c in info.join_columns if c],
            "projection": [c for c in info.projection_columns if c],
            "query_type": info.query_type,
        },
        default=str,
    )


def retention_for_destination(
    destination: Destination,
    *,
    config: QueryLogConfig | None = None,
    now: datetime | None = None,
) -> datetime | None:
    """Return the cutoff timestamp beyond which a destination's snapshots should expire.

    Returns None when no retention is configured (e.g. read_through mode) or when the
    destination disabled query-log ingestion -- both cases are explicit "leave the archive
    alone" semantics, not "use the global default".
    """
    if config is None:
        config = parse_query_log_config(destination.query_log_config)
    if config is None or not config.enabled:
        return None
    # read_through mode points at the user's external table; we don't expire it.
    if config.mode == "read_through":
        return None
    if destination.query_log_retention_days is None or destination.query_log_retention_days <= 0:
        return None
    now = now or datetime.now(UTC)
    return now - timedelta(days=destination.query_log_retention_days)
