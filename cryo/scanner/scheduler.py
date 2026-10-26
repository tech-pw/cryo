from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from cryo.db.models import MaintenanceRun
from cryo.db.repositories import datalake
from cryo.db.repositories import scanner as scanner_repo
from cryo.db.session import SessionLocal
from cryo.queue.scan_queue import ScanQueue
from cryo.scanner.control import enqueue_scan_run
from cryo.scheduler.cron import cron_is_due, db_instant

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanSchedulerTickResult:
    runs_created: int = 0
    chunks_enqueued: int = 0
    run_id: str | None = None


class ScanSchedulerService:
    def __init__(
        self,
        queue: ScanQueue,
        redis_client: Any,
        *,
        session_factory: Any = SessionLocal,
        namespace: str = "cryo",
    ) -> None:
        self._queue = queue
        self._redis = redis_client
        self._session_factory = session_factory
        self._last_run_key = f"{namespace}:scanner:last_run"

    def tick(self, *, now: datetime | None = None) -> ScanSchedulerTickResult:
        current = now or datetime.now(UTC)
        with self._session_factory() as session:
            config = scanner_repo.get_scanner_config(session)
            last_run_at = self._last_run_at(session)
            if not config.enabled or not cron_is_due(
                config.schedule_cron, now=current, last_run_at=last_run_at
            ):
                return ScanSchedulerTickResult()

            payload: dict[str, object] = {
                "scope": "all",
                "source_id": None,
                "table_ids": [],
                "schema_names": [],
                "trigger": "cron",
                "requested_by": "scanner-scheduler",
                "full_scan": config.full_scan_default,
                "cost_full_scan": config.cost_full_scan_default,
            }
            try:
                result = datalake.create_scan_trigger(session, payload)
            except ValueError:
                return ScanSchedulerTickResult()

            run_id = result["run_id"]
            if not isinstance(run_id, UUID):
                run_id = UUID(str(run_id))
            enqueue_result = enqueue_scan_run(
                session,
                self._queue,
                run_id=run_id,
                payload={
                    "scope": "all",
                    "source_id": None,
                    "table_ids": [],
                    "schema_names": [],
                    "trigger": "cron",
                    "full_scan": config.full_scan_default,
                    "cost_full_scan": config.cost_full_scan_default,
                },
                num_chunks=config.num_chunks,
            )
            if enqueue_result.queued:
                self._mark_last_run(current)
                return ScanSchedulerTickResult(
                    runs_created=1,
                    chunks_enqueued=enqueue_result.num_chunks,
                    run_id=str(run_id),
                )
            return ScanSchedulerTickResult()

    def _last_run_at(self, session: Any) -> datetime | None:
        """Newer of the Redis marker and the latest cron scan run in Postgres.

        Redis has no persistence in the bundled chart, so the marker alone re-fired the daily
        fleet scan after every Redis restart (#177).
        """
        candidates = [self._get_last_run(), _last_cron_scan_run(session)]
        present = [db_instant(value) for value in candidates if value is not None]
        return max(present) if present else None

    def _get_last_run(self) -> datetime | None:
        try:
            raw = self._redis.get(self._last_run_key)
        except Exception:
            logger.warning("Redis scan last-run marker read failed")
            return None
        if raw is None:
            return None
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        return datetime.fromisoformat(text.replace("Z", "+00:00"))

    def _mark_last_run(self, run_at: datetime) -> None:
        self._redis.set(self._last_run_key, run_at.isoformat())


def _last_cron_scan_run(session: Any) -> datetime | None:
    try:
        value = session.scalar(
            select(func.max(MaintenanceRun.created_at)).where(
                MaintenanceRun.kind == "scan", MaintenanceRun.trigger == "cron"
            )
        )
    except Exception:
        logger.warning("Could not read the latest cron scan run", exc_info=True)
        return None
    return db_instant(value) if value is not None else None
