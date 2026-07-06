from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.config import Settings, get_settings
from cryo.cost.model import CostStatsInput
from cryo.db.models import Source, Table, TableCost
from cryo.db.session import SessionLocal
from cryo.domain.enums import CostPrecision
from cryo.observability.metrics import scanner_tables_scanned_total
from cryo.scanner.analyze import TableAnalysis, analyze_catalog_table
from cryo.scanner.cost import MetadataCostAccounting, build_table_cost_analysis
from cryo.scanner.orphans import carry_forward_orphans, scan_orphans, should_run_orphan_scan
from cryo.scanner.persistence import (
    already_scanned_today,
    persist_table_analysis,
    record_table_scan_outcome,
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
    cancelled: bool = False


class ScanProgressCallback(Protocol):
    def __call__(self, event: dict[str, Any]) -> None: ...


def deterministic_chunk[T](items: list[T], *, chunk_id: int, num_chunks: int) -> list[T]:
    if num_chunks <= 0:
        raise ValueError("num_chunks must be positive")
    if chunk_id < 0 or chunk_id >= num_chunks:
        raise ValueError("chunk_id must be between 0 and num_chunks - 1")
    return [item for index, item in enumerate(items) if index % num_chunks == chunk_id]


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

    candidates = _discover_candidates(
        session,
        source_id=source_id,
        table_ids=table_ids,
        table_names=table_names,
        schema_names=schema_names,
        source_factory=source_factory,
    )
    assigned = deterministic_chunk(candidates, chunk_id=chunk_id, num_chunks=total_chunks)

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

    if effective_parallel > 0 and len(assigned) > 1:
        return _run_scan_parallel(
            assigned,
            settings=settings,
            full_scan=full_scan,
            cost_full_scan=cost_full_scan,
            scan_at=scan_at,
            max_workers=effective_parallel,
            total_discovered=len(candidates),
            progress_callback=progress_callback,
            cancel_checker=cancel_checker,
        )

    return _run_scan_serial(
        session,
        assigned,
        settings=settings,
        full_scan=full_scan,
        cost_full_scan=cost_full_scan,
        scan_at=scan_at,
        total_discovered=len(candidates),
        progress_callback=progress_callback,
        cancel_checker=cancel_checker,
    )


def _run_scan_serial(
    session: Session,
    assigned: list[ScanCandidate],
    *,
    settings: Settings,
    full_scan: bool,
    cost_full_scan: bool,
    scan_at: datetime,
    total_discovered: int,
    progress_callback: ScanProgressCallback | None,
    cancel_checker: Any | None,
) -> ScanSummary:
    """Scan tables serially within the current session."""
    scanned = skipped = failed = 0

    for candidate in assigned:
        if _cancel_requested(cancel_checker):
            return ScanSummary(
                discovered=total_discovered,
                assigned=len(assigned),
                scanned=scanned,
                skipped=skipped,
                failed=failed,
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
        if not full_scan and already_scanned_today(session, table_id=table_row.id, now=scan_at):
            skipped += 1
            _emit_progress(
                progress_callback,
                {"event": "table_result", "table_ref": table_ref, "outcome": "skipped"},
            )
            continue

        try:
            history = stats_history_for_signals(session, table_row.id)
            analysis = analyze_catalog_table(
                candidate.catalog,
                candidate.schema_name,
                candidate.table_name,
                scan_at=scan_at,
                small_file_mb=settings.scan_small_file_mb,
                file_size_threshold_mb=settings.scan_needs_compaction_threshold,
                settings=settings,
                stats_history=history,
            )
            analysis = _apply_orphan_scan(
                session,
                candidate=candidate,
                table_id=table_row.id,
                analysis=analysis,
                settings=settings,
                scan_at=scan_at,
                cost_full_scan=cost_full_scan,
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
                scanned_at=scan_at,
            )
            _emit_progress(
                progress_callback,
                {
                    "event": "table_result",
                    "table_ref": table_ref,
                    "outcome": "failed",
                    "error": str(exc),
                },
            )

    return ScanSummary(
        discovered=total_discovered,
        assigned=len(assigned),
        scanned=scanned,
        skipped=skipped,
        failed=failed,
    )


def _run_scan_parallel(
    assigned: list[ScanCandidate],
    *,
    settings: Settings,
    full_scan: bool,
    cost_full_scan: bool,
    scan_at: datetime,
    max_workers: int,
    total_discovered: int,
    progress_callback: ScanProgressCallback | None,
    cancel_checker: Any | None,
) -> ScanSummary:
    """Scan tables in parallel using separate DB sessions per table."""
    scanned = skipped = failed = 0

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
            if not full_scan and already_scanned_today(
                table_session, table_id=table_row.id, now=scan_at
            ):
                return ("skipped", None)

            try:
                history = stats_history_for_signals(table_session, table_row.id)
                analysis = analyze_catalog_table(
                    candidate.catalog,
                    candidate.schema_name,
                    candidate.table_name,
                    scan_at=scan_at,
                    small_file_mb=settings.scan_small_file_mb,
                    file_size_threshold_mb=settings.scan_needs_compaction_threshold,
                    settings=settings,
                    stats_history=history,
                )
                analysis = _apply_orphan_scan(
                    table_session,
                    candidate=candidate,
                    table_id=table_row.id,
                    analysis=analysis,
                    settings=settings,
                    scan_at=scan_at,
                    cost_full_scan=cost_full_scan,
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
                    scanned_at=scan_at,
                )
                return ("failed", str(exc))

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
            if result == "scanned":
                scanned += 1
            elif result == "skipped":
                skipped += 1
            elif result == "cancelled":
                _emit_progress(
                    progress_callback,
                    {"event": "table_result", "table_ref": table_ref, "outcome": "skipped"},
                )
                continue
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
    return f"{candidate.schema_name}.{candidate.table_name}"


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


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
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


def _discover_candidates(
    session: Session,
    *,
    source_id: UUID | None,
    table_ids: Sequence[UUID] | None = None,
    table_names: Sequence[tuple[str, str]] | None = None,
    schema_names: Sequence[str] | None = None,
    source_factory: Any,
) -> list[ScanCandidate]:
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

    requested_schemas = set(schema_names or ())
    # Knowing exactly which tables we want also tells us exactly which schemas matter --
    # narrows the live catalog walk below to just those namespaces.
    if requested_tables is not None and not requested_schemas:
        requested_schemas = {schema_name for _, schema_name, _ in requested_tables}

    candidates: list[ScanCandidate] = []
    for source in session.scalars(stmt):
        try:
            catalog = source_factory(source)
            for schema_name, table_name in _list_candidate_tables(catalog, requested_schemas):
                if requested_schemas and schema_name not in requested_schemas:
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
        except Exception:
            logger.exception("Failed to discover tables for source %s", source.name)
    return sorted(
        candidates, key=lambda item: (item.source.name, item.schema_name, item.table_name)
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


def _apply_orphan_scan(
    session: Session,
    *,
    candidate: ScanCandidate,
    table_id: UUID,
    analysis: TableAnalysis,
    settings: Settings,
    scan_at: datetime,
    cost_full_scan: bool,
) -> TableAnalysis:
    if analysis.cost is None:
        return analysis

    previous_cost = session.get(TableCost, table_id)

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
            )
        return analysis

    table_obj = candidate.catalog.load_table(candidate.schema_name, candidate.table_name)
    orphan_result = scan_orphans(
        table_obj,
        source_config=dict(candidate.source.config),
        referenced_file_paths=analysis.cost.referenced_file_paths,
        scan_at=scan_at,
        settings=settings,
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
        )

    return _rebuild_cost_analysis(
        analysis,
        settings=settings,
        orphan_bytes=orphan_result.orphan_bytes,
        listed_bytes=orphan_result.listed_bytes,
        last_full_cost_scan_at=orphan_result.last_full_cost_scan_at,
        precision=CostPrecision.STORAGE_TRUTH,
    )


def _rebuild_cost_analysis(
    analysis: TableAnalysis,
    *,
    settings: Settings,
    orphan_bytes: int | None,
    listed_bytes: int | None,
    last_full_cost_scan_at: datetime | None,
    precision: CostPrecision,
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
        query_count_30d=0,
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
    )
    return replace(analysis, cost=new_cost)


if __name__ == "__main__":
    main()
