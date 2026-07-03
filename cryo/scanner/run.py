from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.config import Settings, get_settings
from cryo.db.models import Source
from cryo.db.session import SessionLocal
from cryo.scanner.analyze import analyze_catalog_table
from cryo.scanner.persistence import already_scanned_today, persist_table_analysis, upsert_table
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
    chunk_id: int = 0,
    num_chunks: int | None = None,
    full_scan: bool = False,
    now: datetime | None = None,
    source_factory: Any = source_catalog_for,
) -> ScanSummary:
    settings = settings or get_settings()
    total_chunks = num_chunks or settings.scan_num_chunks
    scan_at = now or datetime.now(UTC)
    if scan_at.tzinfo is None:
        scan_at = scan_at.replace(tzinfo=UTC)

    candidates = _discover_candidates(session, source_id=source_id, source_factory=source_factory)
    assigned = deterministic_chunk(candidates, chunk_id=chunk_id, num_chunks=total_chunks)
    scanned = skipped = failed = 0

    logger.info(
        "Scanner chunk %s/%s assigned %s of %s discovered tables",
        chunk_id,
        total_chunks,
        len(assigned),
        len(candidates),
    )
    for candidate in assigned:
        table_row = upsert_table(
            session,
            source_id=candidate.source.id,
            schema_name=candidate.schema_name,
            table_name=candidate.table_name,
        )
        if not full_scan and already_scanned_today(session, table_id=table_row.id, now=scan_at):
            skipped += 1
            continue

        try:
            analysis = analyze_catalog_table(
                candidate.catalog,
                candidate.schema_name,
                candidate.table_name,
                scan_at=scan_at,
                small_file_mb=settings.scan_small_file_mb,
                file_size_threshold_mb=settings.scan_needs_compaction_threshold,
            )
            persist_table_analysis(
                session,
                source_id=candidate.source.id,
                analysis=analysis,
                table=table_row,
            )
            session.commit()
            scanned += 1
        except Exception:
            session.rollback()
            failed += 1
            logger.exception(
                "Failed to scan %s.%s from source %s",
                candidate.schema_name,
                candidate.table_name,
                candidate.source.name,
            )

    return ScanSummary(
        discovered=len(candidates),
        assigned=len(assigned),
        scanned=scanned,
        skipped=skipped,
        failed=failed,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan enabled Iceberg sources into Cryo tables.")
    parser.add_argument("--source-id", type=UUID)
    parser.add_argument("--chunk-id", type=int, default=0)
    parser.add_argument("--num-chunks", type=int)
    parser.add_argument("--full-scan", action="store_true")
    args = parser.parse_args(argv)
    if args.num_chunks is not None and args.num_chunks <= 0:
        parser.error("--num-chunks must be positive")
    if args.chunk_id < 0:
        parser.error("--chunk-id must be non-negative")
    if args.num_chunks is not None and args.chunk_id >= args.num_chunks:
        parser.error("--chunk-id must be less than --num-chunks")
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
        )
    logger.info("Scanner finished: %s", summary)


def _discover_candidates(
    session: Session,
    *,
    source_id: UUID | None,
    source_factory: Any,
) -> list[ScanCandidate]:
    stmt = select(Source).where(Source.enabled.is_(True)).order_by(Source.name)
    if source_id is not None:
        stmt = stmt.where(Source.id == source_id)

    candidates: list[ScanCandidate] = []
    for source in session.scalars(stmt):
        try:
            catalog = source_factory(source)
            for schema_name, table_name in catalog.list_tables():
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


if __name__ == "__main__":
    main()
