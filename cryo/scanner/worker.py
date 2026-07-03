from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import MaintenanceRun
from cryo.db.session import SessionLocal
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
    ) -> None:
        self._queue = queue
        self._session_factory = session_factory

    def process_next(self) -> ScanWorkResult | None:
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

            summary = dict(run.summary or {})
            summary.update({"queued": False, "started_at": datetime.now(UTC).isoformat()})
            run.status = "running"
            run.summary = summary
            session.commit()

            try:
                scan_summary = run_scan(
                    session,
                    source_id=_parse_uuid(payload.get("source_id")),
                    table_ids=_parse_uuid_list(payload.get("table_ids")),
                    schema_names=_string_list(payload.get("schema_names")),
                    full_scan=True,
                    cost_full_scan=bool(payload.get("cost_full_scan")),
                )
            except Exception as exc:
                session.rollback()
                run = session.get(MaintenanceRun, parsed_run_id)
                if run is not None:
                    run.status = "failed"
                    run.finished_at = datetime.now(UTC)
                    run.summary = {
                        **dict(run.summary or {}),
                        "queued": False,
                        "error": str(exc),
                    }
                    session.commit()
                logger.exception("Scan run %s failed", run_id)
                return ScanWorkResult(run_id=run_id, status="failed")

            run = session.get(MaintenanceRun, parsed_run_id)
            if run is None:
                return ScanWorkResult(run_id=run_id, status="missing", summary=scan_summary)

            status = "failed" if scan_summary.failed else "succeeded"
            run.status = status
            run.finished_at = datetime.now(UTC)
            run.summary = {
                **dict(run.summary or {}),
                "queued": False,
                "discovered": scan_summary.discovered,
                "assigned": scan_summary.assigned,
                "scanned": scan_summary.scanned,
                "skipped": scan_summary.skipped,
                "failed": scan_summary.failed,
            }
            session.commit()
            return ScanWorkResult(run_id=run_id, status=status, summary=scan_summary)

    def _claim_accepted_run(self) -> dict[str, Any] | None:
        with self._session_factory() as session:
            run = session.scalar(
                select(MaintenanceRun)
                .where(MaintenanceRun.kind == "scan", MaintenanceRun.status == "accepted")
                .order_by(MaintenanceRun.created_at)
                .limit(1)
            )
            if run is None:
                return None
            summary = dict(run.summary or {})
            return {
                "run_id": str(run.id),
                "scope": summary.get("scope", "all"),
                "source_id": summary.get("source_id"),
                "table_ids": summary.get("table_ids", []),
                "schema_names": summary.get("schema_names", []),
                "cost_full_scan": bool(summary.get("cost_full_scan")),
                "trigger": run.trigger,
            }


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
