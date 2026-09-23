from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from cryo.db.models import MaintenanceRun
from cryo.db.repositories import scanner as scanner_repo
from cryo.db.session import SessionLocal
from cryo.observability.correlation import (
    correlation_id_from_summary,
    correlation_scope,
    set_context,
)
from cryo.observability.tracing import trace_operation
from cryo.queue.run_events import (
    RunEventSink,
    build_log_line,
    build_scan_progress,
    safe_publish,
    scan_progress_message,
)
from cryo.queue.scan_queue import ScanQueue
from cryo.scanner.run import ScanSummary, run_scan

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanWorkResult:
    run_id: str | None = None
    status: str | None = None
    summary: ScanSummary | None = None


class ScanWorker:
    """Consumes ad-hoc scan runs from Redis, with DB fallback for stranded runs."""

    def __init__(
        self,
        queue: ScanQueue,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        run_events: RunEventSink | None = None,
    ) -> None:
        self._queue = queue
        self._session_factory = session_factory
        self._run_events = run_events

    def process_next(self) -> ScanWorkResult | None:
        with (
            correlation_scope(clear=True),
            trace_operation("scanner.chunk.execute"),
        ):
            return self._process_next()

    def _process_next(self) -> ScanWorkResult | None:
        payload = self._queue.claim() or self._claim_accepted_run()
        if payload is None:
            return None

        run_id = str(payload.get("run_id") or "")
        if not run_id:
            return ScanWorkResult(status="skipped")

        try:
            parsed_run_id = uuid.UUID(run_id)
        except ValueError:
            logger.warning("Discarding scan queue payload with invalid run_id=%s", run_id)
            return ScanWorkResult(run_id=run_id, status="failed")

        with self._session_factory() as session:
            run = session.get(MaintenanceRun, parsed_run_id)
            if run is None:
                logger.warning("Discarding scan queue payload for missing run %s", run_id)
                return ScanWorkResult(run_id=run_id, status="missing")
            set_context(
                correlation_id=correlation_id_from_summary(run.summary) or run_id,
                run_id=run_id,
            )

            chunk_id = _int_value(payload.get("chunk_id"), default=0)
            # Prefer the run's established chunk count over a stale/missing queue payload so
            # progress accounting never invents phantom chunks mid-run.
            established_chunks = int(dict(run.summary or {}).get("chunks_total") or 0)
            num_chunks = established_chunks or _int_value(payload.get("num_chunks"), default=1)
            already_claimed = bool(payload.get("_already_claimed"))
            if run.status == "cancelled":
                if already_claimed:
                    # Stranded-claim path already flipped the chunk to running; release it.
                    scanner_repo.record_scan_chunk_finished(
                        self._session_factory,
                        parsed_run_id,
                        chunk_id=chunk_id,
                        num_chunks=num_chunks,
                        status="cancelled",
                        summary_update={
                            "discovered": 0,
                            "assigned": 0,
                            "scanned": 0,
                            "skipped": 0,
                            "failed": 0,
                        },
                    )
                return ScanWorkResult(run_id=run_id, status="cancelled")
            if not already_claimed:
                claimed = scanner_repo.try_claim_scan_chunk(
                    self._session_factory,
                    parsed_run_id,
                    chunk_id=chunk_id,
                    num_chunks=num_chunks,
                )
                if not claimed:
                    logger.info(
                        "Skipping scan chunk %s/%s for run %s (already claimed or finished)",
                        chunk_id,
                        num_chunks,
                        run_id[:8],
                    )
                    return ScanWorkResult(run_id=run_id, status="skipped")
            safe_publish(
                self._run_events,
                parsed_run_id,
                build_log_line(
                    run_id=parsed_run_id,
                    chunk_id=chunk_id,
                    message=f"Started scan chunk {chunk_id + 1}/{num_chunks}",
                ),
            )

            def progress(event: dict[str, Any]) -> None:
                scanner_repo.record_scan_progress(
                    self._session_factory,
                    parsed_run_id,
                    chunk_id=chunk_id,
                    num_chunks=num_chunks,
                    event=event,
                )
                message = scan_progress_message(event, chunk_id=chunk_id, num_chunks=num_chunks)
                level = "error" if str(event.get("outcome") or "") == "failed" else "info"
                table_ref = event.get("table_ref")
                safe_publish(
                    self._run_events,
                    parsed_run_id,
                    build_scan_progress(
                        run_id=parsed_run_id,
                        event=str(event.get("event") or ""),
                        message=message,
                        chunk_id=chunk_id,
                        table_ref=str(table_ref) if table_ref else None,
                        level=level,
                    ),
                )
                safe_publish(
                    self._run_events,
                    parsed_run_id,
                    build_log_line(
                        run_id=parsed_run_id,
                        chunk_id=chunk_id,
                        table_ref=str(table_ref) if table_ref else None,
                        level=level,
                        message=message,
                    ),
                )

            def cancel_requested() -> bool:
                return scanner_repo.scan_cancel_requested(self._session_factory, parsed_run_id)

            try:
                scan_summary = run_scan(
                    session,
                    source_id=_parse_uuid(payload.get("source_id")),
                    table_ids=_parse_uuid_list(payload.get("table_ids")),
                    table_names=_table_name_ref_list(payload.get("table_names")),
                    schema_names=_string_list(payload.get("schema_names")),
                    chunk_id=chunk_id,
                    num_chunks=num_chunks,
                    full_scan=bool(payload.get("full_scan", True)),
                    cost_full_scan=bool(payload.get("cost_full_scan")),
                    progress_callback=progress,
                    cancel_checker=cancel_requested,
                )
            except Exception as exc:
                session.rollback()
                scanner_repo.record_scan_chunk_finished(
                    self._session_factory,
                    parsed_run_id,
                    chunk_id=chunk_id,
                    num_chunks=num_chunks,
                    status="failed",
                    summary_update={
                        "discovered": 0,
                        "assigned": 0,
                        "scanned": 0,
                        "skipped": 0,
                        "failed": 1,
                    },
                    error=str(exc),
                )
                safe_publish(
                    self._run_events,
                    parsed_run_id,
                    build_log_line(
                        run_id=parsed_run_id,
                        chunk_id=chunk_id,
                        level="error",
                        message=f"Scan chunk {chunk_id + 1}/{num_chunks} failed: {exc}",
                    ),
                )
                logger.exception("Scan run %s failed", run_id)
                return ScanWorkResult(run_id=run_id, status="failed")

            if scan_summary.cancelled:
                status = "cancelled"
            elif scan_summary.failed:
                status = "partial_failed"
            else:
                status = "succeeded"
            scanner_repo.record_scan_chunk_finished(
                self._session_factory,
                parsed_run_id,
                chunk_id=chunk_id,
                num_chunks=num_chunks,
                status=status,
                summary_update={
                    "discovered": scan_summary.discovered,
                    "assigned": scan_summary.assigned,
                    "scanned": scan_summary.scanned,
                    "skipped": scan_summary.skipped,
                    "failed": scan_summary.failed,
                    "unreadable": scan_summary.unreadable,
                },
            )
            safe_publish(
                self._run_events,
                parsed_run_id,
                build_log_line(
                    run_id=parsed_run_id,
                    chunk_id=chunk_id,
                    level="warning" if status in {"partial_failed", "failed"} else "info",
                    message=(
                        f"Finished scan chunk {chunk_id + 1}/{num_chunks} with status {status}"
                    ),
                ),
            )
            return ScanWorkResult(run_id=run_id, status=status, summary=scan_summary)

    def _claim_accepted_run(self) -> dict[str, Any] | None:
        """DB fallback for stranded runs — atomically claims under row lock."""
        return scanner_repo.claim_next_stranded_scan_chunk(self._session_factory)


def _parse_uuid(value: object) -> uuid.UUID | None:
    if not value:
        return None
    return uuid.UUID(str(value))


def _parse_uuid_list(value: object) -> list[uuid.UUID]:
    if not value:
        return []
    if not isinstance(value, (list, tuple)):
        return []
    return [uuid.UUID(str(item)) for item in value if item]


def _string_list(value: object) -> list[str]:
    if not value:
        return []
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if item]


def _table_name_ref_list(value: object) -> list[tuple[str, str]]:
    if not value or not isinstance(value, (list, tuple)):
        return []
    refs: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        schema_name = item.get("schema_name")
        table_name = item.get("table_name")
        if schema_name and table_name:
            refs.append((str(schema_name), str(table_name)))
    return refs


def _int_value(value: object, *, default: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    if default > 0:
        return parsed if parsed > 0 else default
    return parsed if parsed >= 0 else default
