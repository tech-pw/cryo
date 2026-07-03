from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from cryo.db.repositories import scanner as scanner_repo
from cryo.queue.scan_queue import ScanQueue


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
    except Exception as exc:
        run = scanner_repo.initialize_scan_chunks(
            session, run_id, num_chunks=num_chunks, queued=False
        )
        if run is not None:
            run.summary = {
                **dict(run.summary or {}),
                "queue_error": str(exc),
                "placeholder": False,
            }
            session.commit()
        return ScanEnqueueResult(
            queued=False,
            message=f"Scan request accepted, but queueing failed: {exc}",
            num_chunks=num_chunks,
            error=str(exc),
        )

    scanner_repo.initialize_scan_chunks(session, run_id, num_chunks=num_chunks, queued=True)
    return ScanEnqueueResult(
        queued=True,
        message="Scan request accepted and queued.",
        num_chunks=num_chunks,
    )
