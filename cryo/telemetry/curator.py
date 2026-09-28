"""Query-log curator: bridges Trino, the Redis buffer, the Iceberg archive, and Postgres.

The curator is the only thing that ever writes to Postgres ``query_logs`` in the new pipeline.
It runs once per scheduler tick (default every minute) and does, per destination, in order:

1. **Fetch** from one or more Trino coordinators in a window of
   ``(now - watermark_overlap) -> now`` -- the overlap covers a missed tick + Trino's
   ``query.min-expire-age`` ring buffer churn. The fetch is read-only and never blocks the
   scheduler; failures are logged and the tick moves on.
2. **Sample** using a deterministic hash of ``query_id`` so a crash + re-fetch selects the
   same subset and never double-counts. Sampling is applied *before* the writer so the
   Iceberg archive shrinks on chatty destinations without losing the per-table aggregates
   that drive layout recommendations.
3. **Dedupe** by ``query_id`` against the destination's last-known watermark. The same query
   may show up on multiple ticks (overlap window), on multiple coordinators (rare), and
   may be re-fetched after a transient HTTP error. The writer's append is the only place
   that can advance the watermark, so dedupe is always by Iceberg-side identity.
4. **Append** to Iceberg via :class:`QueryLogIcebergWriter`. Advance the destination's
   ``query_log_watermark`` only on a successful append. Failed appends leave the watermark
   untouched so the next tick re-fetches the same window.
5. **Spill curated** rows to Postgres ``query_logs`` -- a small filtered subset
   (errors, slow queries, queries against unknown tables, sample of the rest). The
   existing :func:`cryo.telemetry.patterns.aggregate_query_patterns` runs over the curated
   table and produces ``query_patterns`` + ``layout_recommendations``, which the Optimize
   page reads.
6. **Retention**: call ``expire_snapshots(older_than=retention_cutoff)`` on the Iceberg
   table. Idempotent; safe to run every tick.

The curator is fail-open: any error in a step is logged and the tick continues. The whole
pipeline is opt-in per destination -- a destination with no ``query_log_config`` is a
no-op, which is the correct default for destinations nobody has explicitly enabled query
ingestion for.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from cryo.db.models import Destination, QueryLog, Source
from cryo.telemetry.iceberg_writer import (
    QueryLogAppendResult,
    QueryLogConfig,
    QueryLogIcebergWriter,
    QueryLogRetentionResult,
    parse_query_log_config,
    rows_from_trino_queries,
    sample_query,
)
from cryo.telemetry.parser import parse_query_predicates

logger = logging.getLogger(__name__)


# Curator-level tuning constants. The destination's ``query_log_config`` can override
# the cadence; everything else is a sensible default for the planner path.
DEFAULT_FETCH_LIMIT = 5000
# Postgres curated insert size. Single-row INSERTs at 1M/day would dominate the metadata
# DB; bulk inserts at this size keep the curator's tail-latency predictable.
DEFAULT_CURATED_BATCH_SIZE = 500
# How many of the Iceberg-appended rows we look at for "summary-worthy" filtering. Capping
# it lets the curator finish the curated-spill pass even if the Iceberg archive grew faster
# than the Postgres insert budget.
MAX_CURATED_ROWS_PER_TICK = 10_000
# Postgres-side retention for the curated table. Smaller than the Iceberg retention by
# design -- the curated table is the hot path for query_patterns aggregation, the Iceberg
# archive is the long-term forensic record. Default 7 days.
DEFAULT_CURATED_RETENTION_DAYS = 7
# Curated-row selection thresholds. A row is "summary-worthy" if it matches any of:
#   * query failed (state != FINISHED)
#   * duration_ms > SLOW_QUERY_MS
#   * bytes_read > LARGE_SCAN_BYTES
#   * references a table Cryo hasn't seen yet (catches the long tail of one-off
#     ``ad-hoc`` queries that don't show up in normal traffic)
SLOW_QUERY_MS = 60_000
LARGE_SCAN_BYTES = 1_000_000_000  # 1 GB
# Sample fraction for "the rest" of queries (those that don't match any threshold above).
# At 1M queries/day even a 1% sample is 10k rows/day of curated data, plenty for pattern
# mining; bumping this is a knob the user has in the config block.
DEFAULT_CURATED_SAMPLE_RATE = 0.01


@dataclass(frozen=True)
class CuratorTickResult:
    """One curator tick's outcome -- surfaced for metrics, logging, and tests."""

    destination_id: str
    rows_fetched: int = 0
    rows_sampled_in: int = 0
    rows_deduped_out: int = 0
    iceberg_appended: int = 0
    iceberg_snapshot_id: int | None = None
    curated_inserted: int = 0
    patterns_updated: int = 0
    retention: QueryLogRetentionResult | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class CuratorSettings:
    """Knobs the operator can set on the curator deployment (env or config)."""

    fetch_limit: int = DEFAULT_FETCH_LIMIT
    curated_batch_size: int = DEFAULT_CURATED_BATCH_SIZE
    max_curated_rows_per_tick: int = MAX_CURATED_ROWS_PER_TICK
    curated_retention_days: int = DEFAULT_CURATED_RETENTION_DAYS
    curated_sample_rate: float = DEFAULT_CURATED_SAMPLE_RATE


class CuratorFetchError(RuntimeError):
    """Raised when a single Trino coordinator fetch fails. Other coordinators in a
    multi-coordinator destination can still succeed; the curator catches this and moves on.
    """


class QueryLogCurator:
    """Stateless per-destination worker. Construct one per curator tick.

    Takes the existing pieces it needs (writer, source catalog, parser) as constructor args
    so tests can swap in fakes. The Redis stream is the only piece kept outside the curator
    because it's shared across destinations; pass the redis client in or use a small wrapper.
    """

    def __init__(
        self,
        *,
        writer: QueryLogIcebergWriter,
        destination: Destination,
        source: Source | None,
        config: QueryLogConfig,
        trino_ingester: Any,
        settings: CuratorSettings | None = None,
    ) -> None:
        self._writer = writer
        self._destination = destination
        self._source = source
        self._config = config
        self._ingester = trino_ingester
        self._settings = settings or CuratorSettings()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        session: Session,
        *,
        since: datetime | None = None,
        now: datetime | None = None,
    ) -> CuratorTickResult:
        """One tick. Returns a result regardless of partial failures (best-effort).

        Async because the underlying Trino ingester is async (HTTP via httpx). The
        scheduler runs this in its existing ``asyncio.run(_run())`` block, so the
        extra async boundary costs nothing.
        """
        now = now or datetime.now(UTC)
        since = since or self._fetch_window_start(now)

        # 1. Fetch (may return [] for read_through mode where the curator never polls Trino).
        try:
            fetched_rows = await self._fetch(since=since, now=now)
        except CuratorFetchError as exc:
            logger.warning(
                "Curator fetch failed for destination %s: %s",
                self._destination.id,
                exc,
            )
            return CuratorTickResult(
                destination_id=str(self._destination.id),
                error=f"fetch: {exc}",
            )

        if not fetched_rows:
            return self._maybe_run_retention(now=now)

        # 2. Sample (deterministic by query_id).
        sampled_in = [
            row
            for row in fetched_rows
            if sample_query(row.get("query_id", ""), self._config.sample_rate)
        ]
        deduped_out = len(fetched_rows) - len(sampled_in)

        # 3. Append to Iceberg. This is the only place that advances the watermark.
        append_result: QueryLogAppendResult | None = None
        if sampled_in:
            append_result = self._writer.append(sampled_in)
            if append_result.error:
                # Don't touch the watermark; next tick re-fetches the same window.
                logger.warning(
                    "Iceberg append failed for destination %s: %s",
                    self._destination.id,
                    append_result.error,
                )
                return CuratorTickResult(
                    destination_id=str(self._destination.id),
                    rows_fetched=len(fetched_rows),
                    rows_sampled_in=len(sampled_in),
                    rows_deduped_out=deduped_out,
                    error=f"iceberg: {append_result.error}",
                )
            self._advance_watermark(session, advance_to=now)

        # 4. Spill curated subset to Postgres and aggregate patterns.
        curated_inserted = 0
        patterns_updated = 0
        if sampled_in and self._source is not None:
            curated_inserted = await self._spill_curated(session, sampled_in)
            patterns_updated = self._aggregate_patterns(session)

        # 5. Retention sweep on the Iceberg table.
        retention = self._run_retention(now=now)

        return CuratorTickResult(
            destination_id=str(self._destination.id),
            rows_fetched=len(fetched_rows),
            rows_sampled_in=len(sampled_in),
            rows_deduped_out=deduped_out,
            iceberg_appended=append_result.rows_appended if append_result else 0,
            iceberg_snapshot_id=append_result.snapshot_id if append_result else None,
            curated_inserted=curated_inserted,
            patterns_updated=patterns_updated,
            retention=retention,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fetch_window_start(self, now: datetime) -> datetime:
        """The earliest ``executed_at`` we ask Trino for. Driven by config overlap.

        The destination's ``query_log_watermark`` is intentionally NOT used as the window
        start: a longer overlap covers both the configured ring-buffer churn and any
        re-fetch-after-error. The writer's append is the only thing that can advance the
        watermark, so a re-fetch of an already-archived window is harmless -- the curator
        dedupes by ``query_id`` against the Iceberg table before writing.
        """
        return now - timedelta(seconds=self._config.watermark_overlap_seconds)

    async def _fetch(self, *, since: datetime, now: datetime) -> list[dict[str, Any]]:
        """Fetch raw query rows from every configured Trino coordinator and dedupe.

        Returns a list of pre-shaped rows ready for the writer. ``read_through`` mode has
        no Trino fetch (the writer isn't used in that mode either) and returns [].
        """
        if self._config.mode == "read_through":
            return []

        endpoints: list[str | None] = list(self._coordinator_endpoints())
        if not endpoints:
            # No explicit coordinator list -- the ingester was constructed with a single
            # URL at boot. Use it as-is.
            endpoints = [None]
        source_id = str(self._destination.id)
        all_rows: dict[str, dict[str, Any]] = {}
        for endpoint in endpoints:
            try:
                fetched = await self._fetch_one(endpoint=endpoint, since=since, now=now)
            except CuratorFetchError as exc:
                logger.warning("Fetch from endpoint=%r failed: %s", endpoint, exc)
                continue
            for row in fetched:
                qid = row.get("query_id")
                if not qid:
                    continue
                # Keep the row with the latest executed_at; queries can re-appear in
                # overlapping windows and we want the freshest version, not the first.
                if qid in all_rows and (all_rows[qid].get("executed_at") or datetime.min) >= (
                    row.get("executed_at") or datetime.min
                ):
                    continue
                all_rows[qid] = row
        # Add source_id after dedupe (coordinator identity is per-endpoint, not per-row).
        for row in all_rows.values():
            row["source_id"] = source_id
        return list(all_rows.values())

    def _coordinator_endpoints(self) -> list[str]:
        """The list of host:port endpoints the curator polls.

        Empty means "use whatever the ingester was constructed with" (single-coordinator
        deployments). Configured endpoints get appended; the curator never round-robins --
        every endpoint is queried every tick so any single coordinator can answer
        independently and the union is complete.
        """
        return list(self._config.telemetry_coordinators)

    async def _fetch_one(
        self, *, endpoint: str | None, since: datetime, now: datetime
    ) -> list[dict[str, Any]]:
        """Fetch + transform raw Trino rows from one endpoint.

        The ingester interface we depend on is the existing
        ``TrinoQueryIngester.fetch_recent_queries`` (already async + returns dicts).
        Multi-coordinator setups construct a separate ingester per endpoint at boot; the
        curator picks one per call.
        """
        try:
            ingester = self._ingester
            if endpoint is not None and getattr(ingester, "override_url", None) is not None:
                ingester = ingester.override_url(endpoint)
            raw = await ingester.fetch_recent_queries(since=since, limit=self._settings.fetch_limit)
        except Exception as exc:  # noqa: BLE001 -- third-party HTTP driver
            raise CuratorFetchError(str(exc)) from exc
        return rows_from_trino_queries(
            source_id=str(self._destination.id),
            queries=raw,
            filter_columns_extractor=parse_query_predicates,
        )

    async def _spill_curated(self, session: Session, rows: Iterable[dict[str, Any]]) -> int:
        """Pick the summary-worthy subset and bulk-insert into Postgres ``query_logs``.

        Returns the count actually inserted (some rows may be skipped when they map to a
        ``table_id`` we can't resolve; that's logged once per tick at the end, not per
        row, to keep the log quiet on a chatty engine). Requires ``self._source`` -- the
        Postgres table's ``source_id`` is FK to ``cryo.sources``, not destinations.
        """
        if self._source is None:
            return 0
        rows = list(rows)
        if not rows:
            return 0
        capped = rows[: self._settings.max_curated_rows_per_tick]
        curated: list[QueryLog] = []
        unknown_tables = 0
        seen_query_ids: set[str] = set()
        worthy_rows: list[dict[str, Any]] = []
        for row in capped:
            qid = row.get("query_id")
            if not qid or qid in seen_query_ids:
                continue
            seen_query_ids.add(qid)
            # bytes_read is unset for every row reaching here (system.runtime.queries has
            # no stats columns on this Trino version -- see fetch_query_stats's docstring),
            # so _is_summary_worthy's LARGE_SCAN_BYTES check can never fire at this point.
            # A row is worthy without it (failed/slow state, or the sample tail); enrichment
            # below fills the real bytes_read on whatever's already worthy, so the row that
            # lands in Postgres carries real stats even though they couldn't gate inclusion.
            if not self._is_summary_worthy(row):
                continue
            worthy_rows.append(row)
        if self._config.fetch_stats_for_curated and worthy_rows:
            worthy_rows = await self._enrich_with_query_stats(worthy_rows)
        for row in worthy_rows:
            table = self._resolve_table(session, row)
            if table is None:
                unknown_tables += 1
            curated.append(
                QueryLog(
                    source_id=self._source.id,
                    table_id=table.id if table is not None else None,
                    query_id=row.get("query_id"),
                    engine=row.get("engine") or "trino",
                    query_text=row.get("query_text"),
                    query_type=row.get("query_type"),
                    filter_columns=None,
                    join_columns=None,
                    projection_columns=None,
                    executed_at=row.get("executed_at") or datetime.now(UTC),
                    duration_ms=row.get("duration_ms"),
                    rows_read=row.get("rows_read"),
                    bytes_read=row.get("bytes_read"),
                    files_read=row.get("files_read"),
                    files_pruned=row.get("files_pruned"),
                    user_name=row.get("user_name"),
                    raw={
                        "ingested_via": "curator",
                        "iceberg_snapshot_id": row.get("_iceberg_snapshot_id"),
                        "state": row.get("state"),
                        "error_code": row.get("error_code"),
                        "query_ranges": _query_ranges(row.get("query_text")),
                        # Real per-table identity from Trino's own query plan (inputs[]),
                        # set only when fetch_stats_for_curated enriched this row -- see
                        # enrich_row_with_query_stats. Kept alongside the SQL-text-derived
                        # table_id above (not yet used to resolve it, see _resolve_table's
                        # own note) so it's queryable/backfillable without re-fetching.
                        "trino_inputs": row.get("_trino_inputs"),
                    },
                )
            )
        if not curated:
            return 0
        # Insert in chunks so a single bad row doesn't tank the whole batch.
        for i in range(0, len(curated), self._settings.curated_batch_size):
            session.add_all(curated[i : i + self._settings.curated_batch_size])
            session.flush()
        if unknown_tables:
            logger.info(
                "Curator curated-spill for destination %s skipped %s summary-worthy rows "
                "referencing tables Cryo hasn't seen yet",
                self._destination.id,
                unknown_tables,
            )
        return len(curated)

    async def _enrich_with_query_stats(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Fill real bytes_read/rows_read/files_read on the curated-worthy subset via one
        Trino /v1/query/{id} call per row, bounded by max_stats_enrichment_per_tick and run
        with limited concurrency so a chatty tick can't open hundreds of connections to
        Trino at once. Best-effort: a failed or timed-out fetch leaves that row exactly as
        rows_from_trino_queries produced it (stats fields None), never raises.
        """
        import asyncio

        import httpx

        from cryo.telemetry.iceberg_writer import enrich_row_with_query_stats

        capped = rows[: self._config.max_stats_enrichment_per_tick]
        rest = rows[self._config.max_stats_enrichment_per_tick :]
        if not capped:
            return rows

        fetch_stats = getattr(self._ingester, "fetch_query_stats", None)
        if fetch_stats is None:
            return rows

        semaphore = asyncio.Semaphore(20)

        async def _enrich_one(client: httpx.AsyncClient, row: dict[str, Any]) -> dict[str, Any]:
            qid = row.get("query_id")
            if not qid:
                return row
            async with semaphore:
                try:
                    detail = await fetch_stats(qid, client=client)
                except TypeError:
                    # Ingester stub in tests may not accept the client kwarg.
                    detail = await fetch_stats(qid)
            return enrich_row_with_query_stats(row, detail) if detail else row

        async with httpx.AsyncClient(
            timeout=30.0,
            auth=getattr(self._ingester, "auth", None),
            verify=getattr(self._ingester, "verify_ssl", True),
        ) as client:
            enriched = await asyncio.gather(*(_enrich_one(client, row) for row in capped))
        return list(enriched) + rest

    def _is_summary_worthy(self, row: dict[str, Any]) -> bool:
        """Apply the curated-row filter.

        Thresholds documented at module top -- a row survives if it matches ANY of:
        state is failed, slow, very large scan, or it's in the sampled tail of normal
        traffic. The sample rate keeps "normal" rows flowing into Postgres at a
        predictable rate independent of Trino traffic.
        """
        state = (row.get("state") or "").upper()
        if state and state not in {"FINISHED", "SUCCESS", "SUCCEEDED"}:
            return True
        if (row.get("duration_ms") or 0) > SLOW_QUERY_MS:
            return True
        if (row.get("bytes_read") or 0) > LARGE_SCAN_BYTES:
            return True
        # Sample the rest at DEFAULT_CURATED_SAMPLE_RATE. Deterministic by query_id so
        # a re-fetch picks the same subset.
        return sample_query(row.get("query_id", ""), self._settings.curated_sample_rate)

    def _resolve_table(self, session: Session, row: dict[str, Any]) -> Any | None:
        """Resolve a query row's referenced table to a ``cryo.tables`` row, if known.

        Prefers ``_trino_inputs`` (real per-table identity from Trino's own query plan,
        set by enrich_row_with_query_stats when fetch_stats_for_curated is on) over
        parsing query_text with a regex: the regex only matches the *first* table
        reference in the SQL and breaks on joins/CTEs/subqueries, whereas Trino's own
        ``inputs[]`` names every table the query actually touched. When a query joins
        multiple tables, the first Iceberg-backed one is used -- Trino also reports
        metadata-table reads (e.g. a table's ``$files``) as separate entries, which are
        stripped here since they're not a real table a preset/candidate would match.
        Falls back to the regex when no enrichment ran (fetch_stats_for_curated off, or
        this particular fetch_query_stats call failed) -- unchanged from before, so a
        destination without enrichment enabled keeps its existing (imperfect) behavior.
        """
        from sqlalchemy import select

        from cryo.db.models import Table as TableModel
        from cryo.telemetry.parser import extract_table_from_query

        trino_inputs = row.get("_trino_inputs")
        if isinstance(trino_inputs, list):
            for entry in trino_inputs:
                schema_name = entry.get("schema")
                table_name = entry.get("table")
                if not schema_name or not table_name or table_name.endswith("$files"):
                    continue
                match = session.scalar(
                    select(TableModel).where(
                        TableModel.schema_name == schema_name,
                        TableModel.table_name == table_name,
                        TableModel.is_active.is_(True),
                    )
                )
                if match is not None:
                    return match

        query_text = row.get("query_text") or ""
        schema_name, table_name = extract_table_from_query(query_text)
        if not schema_name or not table_name:
            return None
        return session.scalar(
            select(TableModel).where(
                TableModel.schema_name == schema_name,
                TableModel.table_name == table_name,
                TableModel.is_active.is_(True),
            )
        )

    def _aggregate_patterns(self, session: Session) -> int:
        """Run the existing pattern aggregator over the curated table.

        Idempotent; cheap on a 7-day Postgres table. Same path the legacy scheduler
        used, just now triggered by the curator instead of an inline ingest.
        """
        from cryo.telemetry.patterns import aggregate_query_patterns

        try:
            return int(aggregate_query_patterns(session, table_id=None) or 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("aggregate_query_patterns failed: %s", exc)
            return 0

    def _run_retention(self, *, now: datetime) -> QueryLogRetentionResult | None:
        """Expire Iceberg snapshots older than the destination's configured retention.

        Returns None for read_through mode (the user's external table is their problem).
        """
        if self._config.mode == "read_through":
            return None
        if self._destination.query_log_retention_days is None:
            return None
        if self._destination.query_log_retention_days <= 0:
            return None
        try:
            return self._writer.expire_snapshots_older_than(
                self._destination.query_log_retention_days
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Retention sweep failed for destination %s: %s",
                self._destination.id,
                exc,
            )
            return QueryLogRetentionResult(
                table_identifier=self._writer.table_identifier,
                error=str(exc),
            )

    def _maybe_run_retention(self, *, now: datetime) -> CuratorTickResult:
        """When no rows were fetched, still run the retention sweep so idle destinations
        don't accumulate snapshots forever.
        """
        retention = self._run_retention(now=now)
        return CuratorTickResult(
            destination_id=str(self._destination.id),
            retention=retention,
        )

    def _advance_watermark(self, session: Session, *, advance_to: datetime) -> None:
        """Update ``Destination.query_log_watermark`` to ``advance_to`` and commit.

        Only call after a successful Iceberg append. A failure leaves the watermark
        untouched so the next tick re-fetches the same window.
        """
        self._destination.query_log_watermark = advance_to
        session.add(self._destination)
        session.commit()


def _query_ranges(query_text: object) -> list[dict[str, object]]:
    if not isinstance(query_text, str) or not query_text:
        return []
    from cryo.telemetry.parser import extract_query_ranges

    return [
        {"column": item.column, "lower": item.lower, "upper": item.upper}
        for item in extract_query_ranges(query_text)
    ]


def curator_for_destination(
    destination: Destination,
    *,
    source: Source | None,
    settings: CuratorSettings | None = None,
) -> QueryLogCurator | None:
    """Build a curator for a destination, or None when the destination isn't configured
    for query-log ingestion.

    The caller is expected to iterate ``for curator in curators: curator.run(session)``
    inside a single per-tick loop. A None result is fine -- it just means "skip".

    A curator can run without a ``Source`` for ``read_through`` mode (which doesn't write
    to Iceberg or Postgres and just hands off to the existing read-through path) or for
    a destination whose operator hasn't yet linked a Source -- in those cases the curator
    skips the curated-spill pass but still runs the fetch + Iceberg archive.
    """
    config = parse_query_log_config(destination.query_log_config)
    if config is None:
        return None
    from cryo.telemetry.iceberg_writer import writer_for_destination

    writer = writer_for_destination(destination, source=source, config=config)
    ingester = _build_ingester(destination, config, source)
    return QueryLogCurator(
        writer=writer,
        destination=destination,
        source=source,
        config=config,
        trino_ingester=ingester,
        settings=settings,
    )


def _build_ingester(destination: Destination, config: QueryLogConfig, source: Source | None) -> Any:
    """Construct the right Trino ingester for this destination.

    Multi-coordinator destinations get a *router* ingester that picks an endpoint per
    request (every endpoint is queried every tick, but the legacy code's round-robin
    behaviour for the write side stays untouched). For the bootstrap the ingester's
    ``override_url`` is a small wrapper that returns a clone pointed at the named
    coordinator.
    """
    from cryo.telemetry.ingestion import TrinoQueryIngester  # local import: avoid cycle

    primary = _primary_trino_url(destination)
    user, password = _trino_auth_from_config(destination.config)
    verify_ssl = _trino_verify_ssl_from_config(destination.config)
    base = (
        TrinoQueryIngester(
            trino_url=primary,
            user=user,
            password=password,
            verify_ssl=verify_ssl,
        )
        if primary
        else None
    )
    if not config.telemetry_coordinators or base is None:
        return base
    return _MultiCoordinatorIngester(base=base, endpoints=list(config.telemetry_coordinators))


def _trino_auth_from_config(raw_config: object) -> tuple[str, str | None]:
    """Extract (user, password) from a destination's ``config`` block.

    Mirrors ``cryo.engines.trino.client._auth_from_config`` -- the same destination config
    already authenticates every other Trino query Cryo runs against it (the ROI gate, the
    scanner, the executor), so the curator's fetch against ``system.runtime.queries`` must
    authenticate the same way. Before this, ``TrinoQueryIngester`` was always constructed
    with no credentials at all, so any coordinator with auth enabled (basic/LDAP -- the
    common case, and true of Trino-Prod) would 401 on every tick. The curator is fail-open
    (logs and moves on), so this failed silently: ``query_log_config`` could be fully
    enabled and the dashboard would still stay empty.
    """
    config = raw_config if isinstance(raw_config, dict) else {}
    auth_block = config.get("auth")
    if isinstance(auth_block, dict):
        auth_type = str(auth_block.get("type") or "").lower()
        password = auth_block.get("password")
        username = auth_block.get("username") or config.get("user") or config.get("username")
        if auth_type == "basic" and username and password:
            return str(username), str(password)
    password = config.get("password")
    if password:
        return str(config.get("user") or "cryo"), str(password)
    return "cryo", None


def _trino_verify_ssl_from_config(raw_config: object) -> bool:
    """Whether to verify TLS certs for this destination's coordinator.

    Defaults to verifying -- only disabled via an explicit ``verify_ssl: false`` in config,
    same opt-out shape as ``Settings.telemetry_trino_ssl_verify`` for the legacy single-
    cluster path.
    """
    config = raw_config if isinstance(raw_config, dict) else {}
    return bool(config.get("verify_ssl", True))


class _MultiCoordinatorIngester:
    """Routes every fetch to a specific endpoint supplied at call time.

    The curator's existing ``override_url`` shim returns a thin wrapper that pins the
    underlying ``trino_url``. Implemented here rather than in ``TrinoQueryIngester`` so
    we don't perturb the legacy single-endpoint code.
    """

    def __init__(self, *, base: Any, endpoints: list[str]) -> None:
        self._base = base
        self._endpoints = list(endpoints)

    def override_url(self, url: str) -> Any:
        from cryo.telemetry.ingestion import TrinoQueryIngester

        # Clone every field the base ingester was constructed with, not just user/catalog/
        # schema/headers -- a coordinator behind auth needs the password too (self._base.auth
        # is the (user, password) tuple httpx uses; None means no basic auth was configured).
        password = self._base.auth[1] if self._base.auth else None
        return TrinoQueryIngester(
            trino_url=url,
            user=self._base.user,
            password=password,
            catalog=self._base.catalog,
            schema=self._base.schema,
            headers=dict(self._base.headers),
            verify_ssl=self._base.verify_ssl,
        )

    async def fetch_recent_queries(self, *, since: datetime, limit: int) -> list[dict[str, Any]]:
        # Not used: the curator calls override_url(...) per endpoint. We still expose
        # this method for the legacy code path that uses a single ingester directly.
        all_rows: list[dict[str, Any]] = []
        for endpoint in self._endpoints:
            cloned = self.override_url(endpoint)
            rows = await cloned.fetch_recent_queries(since=since, limit=limit)
            all_rows.extend(rows)
        return all_rows


def _primary_trino_url(destination: Destination) -> str | None:
    """Best-effort Trino URL extraction from the destination's config block.

    The legacy Trino destinations store their URL under ``url`` (older versions stored
    ``coordinators`` instead). We accept either so the curator works for both.
    """
    config = destination.config or {}
    if isinstance(config, dict):
        url = config.get("url")
        if isinstance(url, str) and url:
            return url
        coordinators = config.get("coordinators")
        if isinstance(coordinators, list) and coordinators:
            first = coordinators[0]
            if isinstance(first, dict):
                host = first.get("host")
                port = first.get("port")
                scheme = first.get("http_scheme") or "http"
                if host:
                    return f"{scheme}://{host}:{port or 8080}"
            if isinstance(first, str):
                return first
    return None


def purge_old_curated_rows(
    session: Session,
    *,
    retention_days: int = DEFAULT_CURATED_RETENTION_DAYS,
    now: datetime | None = None,
) -> int:
    """Delete Postgres ``query_logs`` rows older than ``retention_days``.

    Run daily from the scheduler. Cheap because the table is partitioned by executed_at
    (see migration adding the index). Returns the deleted row count for metrics.
    """
    from sqlalchemy import delete

    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=retention_days)
    result = session.execute(delete(QueryLog).where(QueryLog.executed_at < cutoff))
    session.commit()
    return int(getattr(result, "rowcount", 0) or 0)
