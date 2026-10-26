from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, NoReturn, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.config import Settings, get_settings
from cryo.cost.model import CostStatsInput
from cryo.db.model_utils import catalog_name_for_source, qualified_name
from cryo.db.models import Source, Table, TableCost, TableStats
from cryo.db.repositories.sources import intersect_requested_schemas, source_allowed_schemas
from cryo.db.session import SessionLocal
from cryo.domain.enums import CostPrecision
from cryo.integrations.aws_credentials import credentials_for_source
from cryo.integrations.aws_s3_metrics import fetch_s3_request_prices
from cryo.observability.logging import configure_process_logging
from cryo.observability.metrics import scanner_tables_scanned_total
from cryo.optimizer.wair.ranges import wair_sample_columns
from cryo.scanner.analyze import TableAnalysis, analyze_catalog_table
from cryo.scanner.cost import MetadataCostAccounting, build_table_cost_analysis
from cryo.scanner.failure_classifier import scan_failure_is_permanent
from cryo.scanner.new_tables import candidate_key, record_new_tables_on_default, unregistered_keys
from cryo.scanner.orphans import carry_forward_orphans, scan_orphans, should_run_orphan_scan
from cryo.scanner.persistence import (
    already_scanned_today,
    needs_weekly_reconcile,
    persist_table_analysis,
    record_table_scan_outcome,
    scan_retry_deferred,
    stats_history_for_signals,
    upsert_table,
)
from cryo.sources.base import SourceCatalog
from cryo.sources.factory import source_catalog_for

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanCandidate:
    source: Source
    catalog: SourceCatalog
    schema_name: str
    table_name: str


@dataclass(frozen=True)
class ScanSummary:
    discovered: int = 0
    assigned: int = 0
    scanned: int = 0
    skipped: int = 0
    failed: int = 0
    # Tables whose metadata is gone from object storage -- counted apart from `failed` because
    # retrying cannot fix them. See cryo/scanner/failure_classifier.py.
    unreadable: int = 0
    # Failing tables not yet due on the retry ladder (persistence.SCAN_RETRY_LADDER).
    deferred: int = 0
    cancelled: bool = False
    # Sources whose table discovery failed (timeout, metastore transport error, bad
    # credentials). Their tables are not in `discovered`, so a metastore outage must not look
    # like a clean scan of an empty catalog (#162). main() exits non-zero when this is set.
    sources_failed: int = 0
    discovery_errors: tuple[str, ...] = ()
    # Newly registered tables whose effective preset is the default (#119).
    new_tables_on_default: int = 0


class ScanProgressCallback(Protocol):
    def __call__(self, event: dict[str, Any]) -> None: ...


_SCAN_ANALYZE_EXECUTOR: ThreadPoolExecutor | None = None


def _scan_analyze_executor() -> ThreadPoolExecutor:
    """Shared pool for catalog/S3-bound scan work, isolated from per-table scan threads.

    Using a separate pool (rather than reusing the outer parallel-scan executor) means a
    table submitted here from within a parallel `scan_one` worker can't deadlock against
    that worker's own pool capacity.
    """
    global _SCAN_ANALYZE_EXECUTOR
    if _SCAN_ANALYZE_EXECUTOR is None:
        _SCAN_ANALYZE_EXECUTOR = ThreadPoolExecutor(
            max_workers=8, thread_name_prefix="scan-analyze"
        )
    return _SCAN_ANALYZE_EXECUTOR


def _run_bounded[R](fn: Callable[[], R], *, timeout_seconds: float, description: str) -> R:
    """Run `fn` with a hard wall-clock bound.

    PyIceberg/boto3 catalog clients don't reliably enforce their own timeouts against a slow
    or unresponsive Hive Metastore/S3 endpoint. Without this bound, a single bad table can
    wedge the scanner daemon's single-threaded poll loop indefinitely (seen in production:
    one table hung for 100+ minutes and starved every subsequently queued scan). A timed-out
    call keeps running in the background executor -- it's abandoned, not cancelled -- but the
    caller regains control instead of blocking forever.
    """
    future = _scan_analyze_executor().submit(fn)
    try:
        return future.result(timeout=timeout_seconds)
    except FuturesTimeoutError as exc:
        raise TimeoutError(
            f"{description} exceeded {timeout_seconds:.0f}s and was abandoned"
        ) from exc


def deterministic_chunk[T](items: list[T], *, chunk_id: int, num_chunks: int) -> list[T]:
    if num_chunks <= 0:
        raise ValueError("num_chunks must be positive")
    if chunk_id < 0 or chunk_id >= num_chunks:
        raise ValueError("chunk_id must be between 0 and num_chunks - 1")
    return [item for index, item in enumerate(items) if index % num_chunks == chunk_id]


def stable_chunk(
    candidates: list[ScanCandidate], *, chunk_id: int, num_chunks: int
) -> list[ScanCandidate]:
    """This chunk's tables, chosen by a hash of each table's identity.

    Membership depends only on (source id, schema, table), never on scan state. The old
    index-modulo slice of the staleness-sorted list changed under the run's own writes:
    chunk 0 refreshed scan_at for its tables, chunk 1 re-sorted and sliced a different
    order, and in one 4-chunk run 11 of 38 tables fell into no chunk while others were
    scanned twice (#154). Stalest-first ordering still applies inside each chunk.
    """
    if num_chunks <= 0:
        raise ValueError("num_chunks must be positive")
    if chunk_id < 0 or chunk_id >= num_chunks:
        raise ValueError("chunk_id must be between 0 and num_chunks - 1")
    if num_chunks == 1:
        return list(candidates)
    return [candidate for candidate in candidates if _chunk_of(candidate, num_chunks) == chunk_id]


def _chunk_of(candidate: ScanCandidate, num_chunks: int) -> int:
    key = f"{candidate.source.id}/{candidate.schema_name}/{candidate.table_name}"
    digest = hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()
    return int(digest, 16) % num_chunks


def order_by_staleness(session: Session, candidates: list[ScanCandidate]) -> list[ScanCandidate]:
    """Sort candidates stalest-first, never-scanned before everything else.

    _discover_candidates returns catalog order (source, schema, table), which correlates with
    nothing an operator cares about. When a run cannot finish every table -- the normal case
    at 12.6k tables -- catalog order means the tables that get scanned are decided by
    alphabet, and a table late in the sort can starve indefinitely while an early one is
    rescanned every six hours. Live 2026-09-22: 686 tables were scanned all 7 days of the
    week while 687 were scanned on exactly one, and 11% of the fleet had drifted past 30 days.

    Stalest-first makes the run self-correcting instead: whatever it does not reach this pass
    is nearer the front of the next one. Same idea as Amoro's `balanced` scheduling policy.
    Ordering is total (staleness, then catalog order). It decides the order within a chunk
    only: chunk membership comes from stable_chunk, because each chunk's own writes change
    the fleet state the next chunk would sort by (#154).
    """
    if not candidates:
        return []

    last_scan: dict[tuple[UUID, str, str], datetime] = {
        (source_id, schema_name, table_name): scan_at
        for source_id, schema_name, table_name, scan_at in session.execute(
            select(Table.source_id, Table.schema_name, Table.table_name, TableStats.scan_at).join(
                TableStats, TableStats.table_id == Table.id
            )
        ).all()
        if scan_at is not None
    }

    def sort_key(candidate: ScanCandidate) -> tuple[int, float, str, str, str]:
        scan_at = last_scan.get((candidate.source.id, candidate.schema_name, candidate.table_name))
        # Never-scanned sorts ahead of every scanned table; among scanned, oldest first.
        never_scanned = 0 if scan_at is None else 1
        age_key = 0.0 if scan_at is None else scan_at.timestamp()
        return (
            never_scanned,
            age_key,
            candidate.source.name,
            candidate.schema_name,
            candidate.table_name,
        )

    return sorted(candidates, key=sort_key)


def run_scan(
    session: Session,
    *,
    settings: Settings | None = None,
    source_id: UUID | None = None,
    table_ids: Sequence[UUID] | None = None,
    table_names: Sequence[tuple[str, str]] | None = None,
    schema_names: Sequence[str] | None = None,
    chunk_id: int = 0,
    num_chunks: int | None = None,
    full_scan: bool = False,
    cost_full_scan: bool = False,
    now: datetime | None = None,
    source_factory: Any = source_catalog_for,
    parallel_tables: int | None = None,
    progress_callback: ScanProgressCallback | None = None,
    cancel_checker: Any | None = None,
) -> ScanSummary:
    """Run a scan pass over assigned tables.

    Args:
        session: Database session for persisting results.
        settings: Scanner settings.
        source_id: Optional filter to scan only one source.
        table_names: (schema_name, table_name) pairs to target directly by name -- unlike
            table_ids, these don't need to already exist as a Table row, so this is how a table
            that's never been scanned before can still be targeted individually. Requires
            source_id (the same name could exist in more than one lakehouse).
        chunk_id: Which chunk this scanner instance handles (0-indexed).
        num_chunks: Total number of scanner chunks (for distributed scanning).
        full_scan: If True, rescan tables already scanned today.
        cost_full_scan: If True, run storage-truth orphan scans.
        now: Override scan timestamp (for testing).
        source_factory: Factory function for creating catalog instances.
        parallel_tables: Number of tables to scan in parallel within this chunk.
            None = use settings.scan_parallel_tables (default).
            0 = serial, >0 = parallel with that many workers.
            Parallel mode uses separate DB sessions per table to avoid conflicts.
    """
    settings = settings or get_settings()
    total_chunks = num_chunks or settings.scan_num_chunks
    effective_parallel = (
        parallel_tables if parallel_tables is not None else settings.scan_parallel_tables
    )
    scan_at = now or datetime.now(UTC)
    if scan_at.tzinfo is None:
        scan_at = scan_at.replace(tzinfo=UTC)

    candidates, discovery_errors = _discover_candidates(
        session,
        source_id=source_id,
        table_ids=table_ids,
        table_names=table_names,
        schema_names=schema_names,
        source_factory=source_factory,
    )
    if source_id is not None and not candidates and discovery_errors:
        # A source-scoped chunk (every queued scan chunk is) that discovers zero tables
        # because the catalog connection itself failed (bad credentials, bad uri, metastore
        # down, ...) must not be reported as a successful empty scan -- that swallowed the
        # real error and let a broken source silently "succeed" with 0 tables. Raising here
        # routes it through the caller's existing failure handling instead.
        raise RuntimeError("; ".join(discovery_errors))
    if discovery_errors:
        logger.error(
            "Scanner chunk %s/%s: table discovery failed for %d source(s): %s",
            chunk_id,
            total_chunks,
            len(discovery_errors),
            "; ".join(discovery_errors),
        )
    assigned = order_by_staleness(
        session, stable_chunk(candidates, chunk_id=chunk_id, num_chunks=total_chunks)
    )
    honor_retry_backoff = not full_scan and table_ids is None and table_names is None

    logger.info(
        "Scanner chunk %s/%s assigned %s of %s discovered tables (parallel=%s)",
        chunk_id,
        total_chunks,
        len(assigned),
        len(candidates),
        effective_parallel,
    )
    _emit_progress(
        progress_callback,
        {
            "event": "assigned",
            "chunk_id": chunk_id,
            "num_chunks": total_chunks,
            "discovered": len(candidates),
            "assigned": len(assigned),
        },
    )

    new_keys = _unregistered_candidates(session, assigned)
    summary = _run_assigned(
        session,
        assigned,
        settings=settings,
        full_scan=full_scan,
        honor_retry_backoff=honor_retry_backoff,
        cost_full_scan=cost_full_scan,
        scan_at=scan_at,
        effective_parallel=effective_parallel,
        total_discovered=len(candidates),
        progress_callback=progress_callback,
        cancel_checker=cancel_checker,
    )
    if new_keys:
        summary = replace(
            summary, new_tables_on_default=_record_new_tables_on_default(session, new_keys)
        )
    if discovery_errors:
        summary = replace(
            summary,
            sources_failed=len(discovery_errors),
            discovery_errors=tuple(discovery_errors),
        )
    return summary


def _unregistered_candidates(
    session: Session, assigned: Sequence[ScanCandidate]
) -> set[tuple[UUID, str, str]]:
    try:
        return unregistered_keys(session, (candidate_key(item) for item in assigned))
    except Exception:
        logger.warning("Could not snapshot unregistered tables for this scan", exc_info=True)
        session.rollback()
        return set()


def _record_new_tables_on_default(session: Session, keys: set[tuple[UUID, str, str]]) -> int:
    try:
        return record_new_tables_on_default(session, keys)
    except Exception:
        logger.warning("Could not record new tables on the default preset", exc_info=True)
        session.rollback()
        return 0


def _run_assigned(
    session: Session,
    assigned: list[ScanCandidate],
    *,
    settings: Settings,
    full_scan: bool,
    honor_retry_backoff: bool,
    cost_full_scan: bool,
    scan_at: datetime,
    effective_parallel: int,
    total_discovered: int,
    progress_callback: ScanProgressCallback | None,
    cancel_checker: Any | None,
) -> ScanSummary:
    if effective_parallel > 0 and len(assigned) > 1:
        return _run_scan_parallel(
            assigned,
            settings=settings,
            full_scan=full_scan,
            honor_retry_backoff=honor_retry_backoff,
            cost_full_scan=cost_full_scan,
            scan_at=scan_at,
            max_workers=effective_parallel,
            total_discovered=total_discovered,
            progress_callback=progress_callback,
            cancel_checker=cancel_checker,
        )

    return _run_scan_serial(
        session,
        assigned,
        settings=settings,
        full_scan=full_scan,
        honor_retry_backoff=honor_retry_backoff,
        cost_full_scan=cost_full_scan,
        scan_at=scan_at,
        total_discovered=total_discovered,
        progress_callback=progress_callback,
        cancel_checker=cancel_checker,
    )


def _run_scan_serial(
    session: Session,
    assigned: list[ScanCandidate],
    *,
    settings: Settings,
    full_scan: bool,
    honor_retry_backoff: bool,
    cost_full_scan: bool,
    scan_at: datetime,
    total_discovered: int,
    progress_callback: ScanProgressCallback | None,
    cancel_checker: Any | None,
) -> ScanSummary:
    """Scan tables serially within the current session."""
    scanned = skipped = failed = unreadable = deferred = 0
    live_price_cache: dict[UUID, float | None] = {}

    for candidate in assigned:
        if _cancel_requested(cancel_checker):
            return ScanSummary(
                discovered=total_discovered,
                assigned=len(assigned),
                scanned=scanned,
                skipped=skipped,
                failed=failed,
                unreadable=unreadable,
                deferred=deferred,
                cancelled=True,
            )

        table_ref = _table_ref(candidate)
        _emit_progress(progress_callback, {"event": "start_table", "table_ref": table_ref})
        table_row = upsert_table(
            session,
            source_id=candidate.source.id,
            schema_name=candidate.schema_name,
            table_name=candidate.table_name,
        )
        if honor_retry_backoff and scan_retry_deferred(table_row, now=scan_at):
            deferred += 1
            _emit_progress(
                progress_callback,
                {"event": "table_result", "table_ref": table_ref, "outcome": "skipped"},
            )
            continue
        if (
            not full_scan
            and already_scanned_today(session, table_id=table_row.id, now=scan_at)
            and not needs_weekly_reconcile(session, table_id=table_row.id, now=scan_at)
        ):
            skipped += 1
            _emit_progress(
                progress_callback,
                {"event": "table_result", "table_ref": table_ref, "outcome": "skipped"},
            )
            continue

        try:
            history = stats_history_for_signals(session, table_row.id)
            wair_columns = _wair_columns(session, table_row.id)

            def _analyze(
                candidate: ScanCandidate = candidate,
                history: Sequence[Mapping[str, Any]] | None = history,
                wair_columns: Sequence[str] = wair_columns,
            ) -> TableAnalysis:
                return analyze_catalog_table(
                    candidate.catalog,
                    candidate.schema_name,
                    candidate.table_name,
                    scan_at=scan_at,
                    small_file_mb=settings.scan_small_file_mb,
                    file_size_threshold_mb=settings.scan_needs_compaction_threshold,
                    settings=settings,
                    stats_history=history,
                    source_config=candidate.source.config,
                    wair_columns=wair_columns,
                )

            analysis = _run_bounded(
                _analyze,
                timeout_seconds=settings.scan_table_timeout_seconds,
                description=f"analyze_catalog_table({table_ref})",
            )
            analysis = _apply_orphan_scan(
                session,
                candidate=candidate,
                table_id=table_row.id,
                analysis=analysis,
                settings=settings,
                scan_at=scan_at,
                cost_full_scan=cost_full_scan,
                live_price_cache=live_price_cache,
            )
            persist_table_analysis(
                session,
                source_id=candidate.source.id,
                analysis=analysis,
                table=table_row,
            )
            record_table_scan_outcome(
                session,
                source_id=candidate.source.id,
                schema_name=candidate.schema_name,
                table_name=candidate.table_name,
                error=None,
                scanned_at=scan_at,
            )
            session.commit()
            scanned += 1
            scanner_tables_scanned_total.labels(source=candidate.source.name).inc()
            _emit_progress(
                progress_callback,
                {"event": "table_result", "table_ref": table_ref, "outcome": "scanned"},
            )
        except Exception as exc:
            session.rollback()
            outcome = "unreadable" if scan_failure_is_permanent(str(exc)) else "failed"
            if outcome == "unreadable":
                unreadable += 1
                logger.warning(
                    "Table metadata missing in object storage for %s.%s from source %s: %s",
                    candidate.schema_name,
                    candidate.table_name,
                    candidate.source.name,
                    exc,
                )
            else:
                failed += 1
                logger.exception(
                    "Failed to scan %s.%s from source %s",
                    candidate.schema_name,
                    candidate.table_name,
                    candidate.source.name,
                )
            _record_scan_failure(
                session,
                source_id=candidate.source.id,
                schema_name=candidate.schema_name,
                table_name=candidate.table_name,
                error=str(exc),
                error_kind=outcome,
                scanned_at=scan_at,
            )
            _emit_progress(
                progress_callback,
                {
                    "event": "table_result",
                    "table_ref": table_ref,
                    "outcome": outcome,
                    "error": str(exc),
                },
            )

    return ScanSummary(
        discovered=total_discovered,
        assigned=len(assigned),
        scanned=scanned,
        skipped=skipped,
        failed=failed,
        unreadable=unreadable,
        deferred=deferred,
    )


def _run_scan_parallel(
    assigned: list[ScanCandidate],
    *,
    settings: Settings,
    full_scan: bool,
    honor_retry_backoff: bool,
    cost_full_scan: bool,
    scan_at: datetime,
    max_workers: int,
    total_discovered: int,
    progress_callback: ScanProgressCallback | None,
    cancel_checker: Any | None,
) -> ScanSummary:
    """Scan tables in parallel using separate DB sessions per table."""
    scanned = skipped = failed = unreadable = deferred = 0
    # Shared across worker threads (GIL makes individual dict get/set safe enough for a
    # best-effort cache; a rare race just costs one redundant AWS lookup, not a correctness
    # bug -- see _resolve_live_s3_get_price's docstring).
    live_price_cache: dict[UUID, float | None] = {}

    def scan_one(candidate: ScanCandidate) -> tuple[str, str | None]:
        """Scan a single table, returning outcome and optional error."""
        if _cancel_requested(cancel_checker):
            return ("cancelled", None)
        with SessionLocal() as table_session:
            table_row = upsert_table(
                table_session,
                source_id=candidate.source.id,
                schema_name=candidate.schema_name,
                table_name=candidate.table_name,
            )
            if honor_retry_backoff and scan_retry_deferred(table_row, now=scan_at):
                return ("deferred", None)
            if (
                not full_scan
                and already_scanned_today(table_session, table_id=table_row.id, now=scan_at)
                and not needs_weekly_reconcile(table_session, table_id=table_row.id, now=scan_at)
            ):
                return ("skipped", None)

            try:
                history = stats_history_for_signals(table_session, table_row.id)
                wair_columns = _wair_columns(table_session, table_row.id)
                table_ref = _table_ref(candidate)
                analysis = _run_bounded(
                    lambda: analyze_catalog_table(
                        candidate.catalog,
                        candidate.schema_name,
                        candidate.table_name,
                        scan_at=scan_at,
                        small_file_mb=settings.scan_small_file_mb,
                        file_size_threshold_mb=settings.scan_needs_compaction_threshold,
                        settings=settings,
                        stats_history=history,
                        source_config=candidate.source.config,
                        wair_columns=wair_columns,
                    ),
                    timeout_seconds=settings.scan_table_timeout_seconds,
                    description=f"analyze_catalog_table({table_ref})",
                )
                analysis = _apply_orphan_scan(
                    table_session,
                    candidate=candidate,
                    table_id=table_row.id,
                    analysis=analysis,
                    settings=settings,
                    scan_at=scan_at,
                    cost_full_scan=cost_full_scan,
                    live_price_cache=live_price_cache,
                )
                persist_table_analysis(
                    table_session,
                    source_id=candidate.source.id,
                    analysis=analysis,
                    table=table_row,
                )
                record_table_scan_outcome(
                    table_session,
                    source_id=candidate.source.id,
                    schema_name=candidate.schema_name,
                    table_name=candidate.table_name,
                    error=None,
                    scanned_at=scan_at,
                )
                table_session.commit()
                scanner_tables_scanned_total.labels(source=candidate.source.name).inc()
                return ("scanned", None)
            except Exception as exc:
                table_session.rollback()
                outcome = "unreadable" if scan_failure_is_permanent(str(exc)) else "failed"
                if outcome == "unreadable":
                    logger.warning(
                        "Table metadata missing in object storage for %s.%s from source %s: %s",
                        candidate.schema_name,
                        candidate.table_name,
                        candidate.source.name,
                        exc,
                    )
                else:
                    logger.exception(
                        "Failed to scan %s.%s from source %s",
                        candidate.schema_name,
                        candidate.table_name,
                        candidate.source.name,
                    )
                _record_scan_failure(
                    table_session,
                    source_id=candidate.source.id,
                    schema_name=candidate.schema_name,
                    table_name=candidate.table_name,
                    error=str(exc),
                    error_kind=outcome,
                    scanned_at=scan_at,
                )
                return (outcome, str(exc))

    with ThreadPoolExecutor(max_workers=min(max_workers, len(assigned))) as executor:
        futures = {}
        for candidate in assigned:
            _emit_progress(
                progress_callback,
                {"event": "start_table", "table_ref": _table_ref(candidate)},
            )
            futures[executor.submit(scan_one, candidate)] = candidate
        for future in as_completed(futures):
            result, error = future.result()
            table_ref = _table_ref(futures[future])
            if result == "deferred":
                deferred += 1
                result = "skipped"
            elif result == "scanned":
                scanned += 1
            elif result == "skipped":
                skipped += 1
            elif result == "cancelled":
                _emit_progress(
                    progress_callback,
                    {"event": "table_result", "table_ref": table_ref, "outcome": "skipped"},
                )
                continue
            elif result == "unreadable":
                unreadable += 1
            else:
                failed += 1
            _emit_progress(
                progress_callback,
                {
                    "event": "table_result",
                    "table_ref": table_ref,
                    "outcome": result,
                    "error": error,
                },
            )
            if _cancel_requested(cancel_checker):
                break

    return ScanSummary(
        discovered=total_discovered,
        assigned=len(assigned),
        scanned=scanned,
        skipped=skipped,
        failed=failed,
        unreadable=unreadable,
        deferred=deferred,
        cancelled=_cancel_requested(cancel_checker),
    )


def _record_scan_failure(
    session: Session,
    *,
    source_id: UUID,
    schema_name: str,
    table_name: str,
    error: str,
    scanned_at: datetime,
    error_kind: str = "failed",
) -> None:
    """Best-effort wrapper around record_table_scan_outcome for the failure path.

    Called right after session.rollback(), which can discard a same-transaction table insert
    from earlier in this attempt -- record_table_scan_outcome re-upserts to handle that, but if
    even that fails (e.g. the session/connection itself is broken), this must not let a
    bookkeeping problem mask the real scan failure being reported to the caller.
    """
    try:
        record_table_scan_outcome(
            session,
            source_id=source_id,
            schema_name=schema_name,
            table_name=table_name,
            error=error,
            error_kind=error_kind,
            scanned_at=scanned_at,
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.exception(
            "Failed to record scan-failure bookkeeping for %s.%s", schema_name, table_name
        )


def _emit_progress(callback: ScanProgressCallback | None, event: dict[str, Any]) -> None:
    if callback is None:
        return
    try:
        callback(event)
    except Exception:
        logger.exception("Scanner progress callback failed")


def _cancel_requested(cancel_checker: Any | None) -> bool:
    if cancel_checker is None:
        return False
    try:
        return bool(cancel_checker())
    except Exception:
        logger.exception("Scanner cancel checker failed")
        return False


def _table_ref(candidate: ScanCandidate) -> str:
    catalog = catalog_name_for_source(candidate.source)
    return qualified_name(catalog, candidate.schema_name, candidate.table_name)


def _wair_columns(session: Session, table_id: UUID) -> list[str]:
    """Columns to sample per-file bounds for, from the last scan and the query-log window."""
    stats = session.get(TableStats, table_id)
    return wair_sample_columns(stats.raw) if stats is not None else []


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan enabled Iceberg sources into Cryo tables.")
    parser.add_argument("--source-id", type=UUID)
    parser.add_argument("--chunk-id", type=int, default=0)
    parser.add_argument("--num-chunks", type=int)
    parser.add_argument("--full-scan", action="store_true")
    parser.add_argument("--cost-full-scan", action="store_true")
    parser.add_argument(
        "--parallel-tables",
        type=int,
        default=0,
        help="Number of tables to scan in parallel (0=serial, default=0)",
    )
    args = parser.parse_args(argv)
    if args.num_chunks is not None and args.num_chunks <= 0:
        parser.error("--num-chunks must be positive")
    if args.chunk_id < 0:
        parser.error("--chunk-id must be non-negative")
    if args.num_chunks is not None and args.chunk_id >= args.num_chunks:
        parser.error("--chunk-id must be less than --num-chunks")
    if args.parallel_tables < 0:
        parser.error("--parallel-tables must be non-negative")
    return args


def main(argv: list[str] | None = None) -> int:
    """One-shot scan; returns the process exit code.

    Non-zero when any source failed discovery, so the Job and CronJob report the failure and
    alerting fires instead of a metastore outage passing as an empty catalog (#162).
    """
    configure_process_logging()
    args = parse_args(argv)
    with SessionLocal() as session:
        summary = run_scan(
            session,
            source_id=args.source_id,
            chunk_id=args.chunk_id,
            num_chunks=args.num_chunks,
            full_scan=args.full_scan,
            cost_full_scan=args.cost_full_scan,
            parallel_tables=args.parallel_tables,
        )
    logger.info("Scanner finished: %s", summary)
    if summary.sources_failed:
        logger.error(
            "Scanner chunk failed: discovery failed for %d source(s)", summary.sources_failed
        )
        return 1
    return 0


def _exit_process(code: int) -> NoReturn:
    """Exit without joining abandoned executor threads.

    _run_bounded abandons a timed-out catalog call, but its worker thread stays blocked in
    the catalog client. concurrent.futures joins its workers at interpreter exit, so with a
    hung metastore socket a finished scanner pod never exited (one ran 7 days, #163).
    Flush logs and streams, then os._exit.
    """
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def _discover_candidates(
    session: Session,
    *,
    source_id: UUID | None,
    table_ids: Sequence[UUID] | None = None,
    table_names: Sequence[tuple[str, str]] | None = None,
    schema_names: Sequence[str] | None = None,
    source_factory: Any,
) -> tuple[list[ScanCandidate], list[str]]:
    stmt = select(Source).where(Source.enabled.is_(True)).order_by(Source.name)
    if source_id is not None:
        stmt = stmt.where(Source.id == source_id)

    requested_tables: set[tuple[UUID, str, str]] | None = None
    if table_ids or table_names:
        requested_tables = set()
        if table_ids:
            requested_tables.update(
                (row.source_id, row.schema_name, row.table_name)
                for row in session.scalars(select(Table).where(Table.id.in_(table_ids)))
            )
        if table_names and source_id is not None:
            # Name-only refs (no Table row/id exists yet -- e.g. a table that's never been
            # scanned before) are only meaningful when scoped to one source; the same
            # schema.table name could otherwise exist in more than one lakehouse. Callers are
            # expected to have validated source_id is set whenever table_names is used (see
            # cryo/db/repositories/datalake.py::_validate_scan_scope).
            requested_tables.update(
                (source_id, schema_name, table_name) for schema_name, table_name in table_names
            )

    # ...and which *sources* matter. Without this the loop below connects to every enabled
    # source even when every requested table lives in one of them, so one unreachable
    # lakehouse stalls scans that never needed it. Confirmed in prod 2026-09-23: a five-table
    # scan of one source sat at scanned=0 for over five minutes burning TCP timeouts against
    # another source's dead metastore (three A records, each timing out in turn) before it could
    # get to any of the tables actually asked for.
    if requested_tables is not None and source_id is None:
        requested_source_ids = {owner for owner, _, _ in requested_tables}
        if requested_source_ids:
            stmt = stmt.where(Source.id.in_(requested_source_ids))

    requested_schemas = set(schema_names or ())
    # Knowing exactly which tables we want also tells us exactly which schemas matter --
    # narrows the live catalog walk below to just those namespaces.
    if requested_tables is not None and not requested_schemas:
        requested_schemas = {schema_name for _, schema_name, _ in requested_tables}

    candidates: list[ScanCandidate] = []
    discovery_errors: list[str] = []
    discovery_timeout = get_settings().scan_table_timeout_seconds
    for source in session.scalars(stmt):
        try:
            allowed = source_allowed_schemas(source)
            if requested_schemas:
                effective_schemas = intersect_requested_schemas(source, requested_schemas)
                if allowed is not None and not effective_schemas:
                    continue
            elif allowed is not None:
                effective_schemas = allowed
            else:
                effective_schemas = set()

            def _open_and_list(
                source: Any = source, effective_schemas: set[str] = effective_schemas
            ) -> tuple[list[tuple[str, str]], Any]:
                catalog = source_factory(source)
                return _list_candidate_tables(catalog, effective_schemas), catalog

            listed, catalog = _run_bounded(
                _open_and_list,
                timeout_seconds=discovery_timeout,
                description=f"discover tables({source.name})",
            )
            for schema_name, table_name in listed:
                if effective_schemas and schema_name not in effective_schemas:
                    continue
                if allowed is not None and schema_name not in allowed:
                    continue
                if (
                    requested_tables is not None
                    and (
                        source.id,
                        schema_name,
                        table_name,
                    )
                    not in requested_tables
                ):
                    continue
                candidates.append(
                    ScanCandidate(
                        source=source,
                        catalog=catalog,
                        schema_name=schema_name,
                        table_name=table_name,
                    )
                )
        except Exception as exc:
            logger.exception("Failed to discover tables for source %s", source.name)
            discovery_errors.append(f"{source.name}: {exc}")
    return (
        sorted(candidates, key=lambda item: (item.source.name, item.schema_name, item.table_name)),
        discovery_errors,
    )


def _list_candidate_tables(catalog: Any, requested_schemas: set[str]) -> list[tuple[str, str]]:
    """Prefer the cheap per-namespace listing when specific schemas are already known -- the
    unscoped list_tables() walks every namespace in the source, which is wasteful (and can be
    genuinely slow against a large Hive Metastore) when only a handful of schemas are in scope.
    Falls back to the full walk for catalogs/test doubles that don't implement the narrower call.
    """
    if not requested_schemas or not hasattr(catalog, "list_tables_in_namespace"):
        return list(catalog.list_tables())
    pairs: list[tuple[str, str]] = []
    for schema_name in sorted(requested_schemas):
        try:
            table_names_in_schema = catalog.list_tables_in_namespace(schema_name)
            pairs.extend((schema_name, table_name) for table_name in table_names_in_schema)
        except Exception:
            logger.exception("Failed to list tables in namespace %s", schema_name)
    return pairs


def _resolve_live_s3_get_price(
    session: Session,
    source_id: UUID,
    settings: Settings,
    cache: dict[UUID, float | None],
) -> float | None:
    """Look up the live S3 GET request price for a source, at most once per scan run.

    `cache` is created fresh per `_run_scan_serial`/`_run_scan_parallel` call and keyed by
    source_id, so a fleet scan across many tables of the same source resolves+decrypts AWS
    credentials once, not once per table (`fetch_s3_request_prices` itself also caches per
    (region, storage_class) for 24h, but that doesn't save the DB lookup+decrypt step).

    Returns ``None`` -- not a fallback numeric value -- when the source has no usable
    ``AWSIntegration``; `project_savings`/`build_table_cost_analysis` already fall back to
    `settings.s3_get_usd_per_1k` themselves when given ``None``, so there is no duplicate
    fallback logic here (see docs/s3-request-cost-tracking.md §6.4).
    """
    if source_id in cache:
        return cache[source_id]
    price: float | None = None
    try:
        credentials = credentials_for_source(session, source_id)
        if credentials is not None:
            info = fetch_s3_request_prices(
                credentials.region,
                credentials,
                fallback_price_per_1k_put=settings.s3_put_usd_per_1k,
                fallback_price_per_1k_get=settings.s3_get_usd_per_1k,
            )
            price = info.price_per_1k_get
    except Exception:
        logger.warning(
            "Failed to resolve live S3 GET price for source %s; this table's cost estimate "
            "will use the configured default rate instead.",
            source_id,
            exc_info=True,
        )
    cache[source_id] = price
    return price


def _apply_orphan_scan(
    session: Session,
    *,
    candidate: ScanCandidate,
    table_id: UUID,
    analysis: TableAnalysis,
    settings: Settings,
    scan_at: datetime,
    cost_full_scan: bool,
    live_price_cache: dict[UUID, float | None],
) -> TableAnalysis:
    if analysis.cost is None:
        return analysis
    # Narrowed to a plain value (not `analysis.cost.…`) so the closure below -- which mypy
    # can't re-narrow an Optional attribute access through -- captures a concrete list.
    referenced_file_paths = analysis.cost.referenced_file_paths

    previous_cost = session.get(TableCost, table_id)
    # TableStats for *this* scan hasn't been persisted yet (persist_table_analysis runs after
    # this function returns) -- the previous scan's query_count_30d is the best available proxy
    # for "how much traffic is this table seeing", same staleness tradeoff already accepted for
    # orphan_bytes via carry_forward_orphans below. Previously this was hardcoded to 0 at every
    # real call site, which made query_request_savings_usd_month always compute to exactly $0
    # in production regardless of price -- a bigger, pre-existing bug than the pricing gap this
    # function is otherwise here to fix.
    previous_stats = session.get(TableStats, table_id)
    query_count_30d = previous_stats.query_count_30d if previous_stats is not None else 0
    live_s3_get_price = _resolve_live_s3_get_price(
        session, candidate.source.id, settings, live_price_cache
    )

    if not should_run_orphan_scan(previous_cost, scan_at, settings, force=cost_full_scan):
        orphan_bytes, listed_bytes, last_full = carry_forward_orphans(previous_cost)
        if orphan_bytes is not None:
            return _rebuild_cost_analysis(
                analysis,
                settings=settings,
                orphan_bytes=orphan_bytes,
                listed_bytes=listed_bytes,
                last_full_cost_scan_at=last_full,
                precision=CostPrecision.METADATA,
                query_count_30d=query_count_30d,
                live_s3_get_price_per_1k=live_s3_get_price,
            )
        return analysis

    def _load_and_scan_orphans() -> Any:
        table_obj = candidate.catalog.load_table(candidate.schema_name, candidate.table_name)
        return scan_orphans(
            table_obj,
            source_config=dict(candidate.source.config),
            referenced_file_paths=referenced_file_paths,
            scan_at=scan_at,
            settings=settings,
        )

    orphan_result = _run_bounded(
        _load_and_scan_orphans,
        timeout_seconds=settings.scan_table_timeout_seconds,
        description=f"orphan scan({candidate.schema_name}.{candidate.table_name})",
    )
    if orphan_result.skipped_reason:
        orphan_bytes, listed_bytes, last_full = carry_forward_orphans(previous_cost)
        return _rebuild_cost_analysis(
            analysis,
            settings=settings,
            orphan_bytes=orphan_bytes,
            listed_bytes=listed_bytes,
            last_full_cost_scan_at=last_full,
            precision=CostPrecision.METADATA,
            query_count_30d=query_count_30d,
            live_s3_get_price_per_1k=live_s3_get_price,
        )

    return _rebuild_cost_analysis(
        analysis,
        settings=settings,
        orphan_bytes=orphan_result.orphan_bytes,
        listed_bytes=orphan_result.listed_bytes,
        last_full_cost_scan_at=orphan_result.last_full_cost_scan_at,
        precision=CostPrecision.STORAGE_TRUTH,
        query_count_30d=query_count_30d,
        live_s3_get_price_per_1k=live_s3_get_price,
    )


def _rebuild_cost_analysis(
    analysis: TableAnalysis,
    *,
    settings: Settings,
    orphan_bytes: int | None,
    listed_bytes: int | None,
    last_full_cost_scan_at: datetime | None,
    precision: CostPrecision,
    query_count_30d: int = 0,
    live_s3_get_price_per_1k: float | None = None,
) -> TableAnalysis:
    if analysis.cost is None:
        return analysis

    metadata_raw = analysis.raw.get("cost", {}).get("metadata", {})
    metadata = MetadataCostAccounting(
        precision=analysis.cost.precision,
        breakdown=analysis.cost.breakdown,
        reclaim_expire_bytes=analysis.cost.reclaim_expire_bytes,
        reclaim_optimize_bytes=analysis.cost.reclaim_optimize_bytes,
        reclaim_manifests_bytes=analysis.cost.reclaim_manifests_bytes,
        small_file_bytes=0,
        referenced_file_paths=analysis.cost.referenced_file_paths,
        raw=metadata_raw if isinstance(metadata_raw, dict) else {},
    )
    stats = CostStatsInput(
        small_files_count=analysis.small_files_count,
        small_file_bytes=0,
        target_file_size_bytes=analysis.target_file_size_bytes or 268_435_456,
        query_count_30d=query_count_30d,
    )
    new_cost = build_table_cost_analysis(
        metadata,
        scan_at=analysis.scan_at,
        settings=settings,
        stats=stats,
        orphan_bytes=orphan_bytes,
        listed_bytes=listed_bytes,
        last_full_cost_scan_at=last_full_cost_scan_at,
        precision=precision,
        live_s3_get_price_per_1k=live_s3_get_price_per_1k,
    )
    # Must also refresh raw["cost"], not just the `.cost` object: `_stats_values()` persists
    # `TableStats.reclaimable_bytes` from `analysis.cost.reclaimable_bytes` but the detailed
    # by-operation/metadata breakdown shown in the UI comes from `analysis.raw["cost"]`. Without
    # this, that breakdown permanently reflects the pre-orphan-scan state (from the original
    # analyze_catalog_table call, before this function's carried-forward or freshly-scanned
    # orphan bytes were folded in) while the scalar total moves on -- the two silently disagree
    # in every persisted row from here on, exactly the drift found in production 2026-08-05.
    new_raw = dict(analysis.raw)
    new_raw["cost"] = new_cost.raw
    return replace(analysis, cost=new_cost, raw=new_raw)


if __name__ == "__main__":
    _exit_process(main())
