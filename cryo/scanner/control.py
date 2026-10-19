from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from cryo.db.repositories import scanner as scanner_repo
from cryo.queue.scan_queue import ScanQueue

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanEnqueueResult:
    queued: bool
    message: str
    num_chunks: int
    error: str | None = None


def enqueue_scan_run(
    session: Session,
    queue: ScanQueue,
    *,
    run_id: uuid.UUID,
    payload: dict[str, Any],
    num_chunks: int,
) -> ScanEnqueueResult:
    try:
        queue.enqueue_chunks(str(run_id), payload, num_chunks=num_chunks)
    except Exception:
        logger.exception("Failed to queue scan %s", run_id)
        run = scanner_repo.initialize_scan_chunks(
            session, run_id, num_chunks=num_chunks, queued=False
        )
        if run is not None:
            run.summary = {
                **dict(run.summary or {}),
                "queue_error": "queueing failed",
                "placeholder": False,
            }
            session.commit()
        return ScanEnqueueResult(
            queued=False,
            message="Scan request accepted, but queueing failed.",
            num_chunks=num_chunks,
            error="queueing failed",
        )

    scanner_repo.initialize_scan_chunks(session, run_id, num_chunks=num_chunks, queued=True)
    return ScanEnqueueResult(
        queued=True,
        message="Scan request accepted and queued.",
        num_chunks=num_chunks,
    )


def reconcile_stale_scan_chunks(
    session_factory: Any,
    queue: ScanQueue,
    *,
    stale_after_seconds: float = 900,
    now: datetime | None = None,
) -> int:
    """Re-queue scan chunks stuck in status='running' with no heartbeat for
    `stale_after_seconds`. A worker pod that dies mid-chunk (redeploy, OOM, node eviction, ...)
    otherwise leaves that chunk claimed forever -- nothing else notices, so the run silently
    stalls with zero active consumers and no visible error. Mirrors the orphan-job recovery
    cryo/queue/reconcile.py already does for the maintenance job queue; the scan queue had no
    equivalent until this.

    Returns the number of chunks re-queued.
    """
    with session_factory() as session:
        stale = scanner_repo.find_stale_running_chunks(
            session, stale_after_seconds=stale_after_seconds, now=now
        )

    for chunk in stale:
        try:
            queue.enqueue(
                str(chunk.run_id),
                {
                    **chunk.payload,
                    "chunk_id": chunk.chunk_id,
                    "num_chunks": chunk.num_chunks,
                },
            )
        except Exception:
            logger.exception(
                "Failed to re-queue stale scan chunk %s/%s for run %s",
                chunk.chunk_id,
                chunk.num_chunks,
                chunk.run_id,
            )
            continue
        scanner_repo.mark_chunk_requeued(
            session_factory,
            chunk.run_id,
            chunk_id=chunk.chunk_id,
            num_chunks=chunk.num_chunks,
        )
        logger.warning(
            "Re-queued stale scan chunk %s/%s for run %s (no heartbeat since %s)",
            chunk.chunk_id,
            chunk.num_chunks,
            chunk.run_id,
            chunk.last_heartbeat_at,
        )

    return len(stale)
