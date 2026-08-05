"""Governor-driven Kubernetes Job dispatch for ad-hoc scan chunks.

Mirrors native_admit.py's admit-then-dispatch shape for the scanner: claim
one payload from the ScanQueue (Redis, with the existing DB-stranded
fallback), size a Kubernetes Job from the target table's last-known
TableStats (or a cheap manifest-count estimate for a never-scanned table),
and dispatch via cryo.scanner.job_dispatch.create_scan_job.

Disabled by default (CRYO_SCANNER_JOB_DISPATCH_ENABLED) -- see the
cost-driven-autoscaling rollout plan. A Job killed mid-run (spot preemption,
node loss) needs no new recovery code: its chunk simply stays non-terminal
and gets picked back up by the same DB-stranded-chunk fallback used here,
the same way ScanWorker.process_next() already recovers stranded chunks
today.
"""

from __future__ import annotations

import logging
import os
import uuid as uuid_module
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from cryo.db.models import TableStats
from cryo.db.repositories import scanner as scanner_repo
from cryo.engines.native.resources import ResourceCeilings
from cryo.queue.scan_queue import ScanQueue
from cryo.scanner.job_dispatch import create_scan_job
from cryo.scanner.resources import estimate_scan_resources

logger = logging.getLogger(__name__)

_ENABLED_ENV = "CRYO_SCANNER_JOB_DISPATCH_ENABLED"
_TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ScanAdmitResult:
    admitted: int = 0
    empty: bool = False


def scan_job_dispatch_enabled(config: Mapping[str, Any] | None = None) -> bool:
    cfg = config or {}
    if "scanner_job_dispatch_enabled" in cfg:
        return bool(cfg["scanner_job_dispatch_enabled"])
    return os.environ.get(_ENABLED_ENV, "").strip().lower() in _TRUE_VALUES


def admit_scan_jobs(
    *,
    queue: ScanQueue,
    session: Session,
    session_factory: Callable[[], Session],
    k8s_config: Mapping[str, Any] | None = None,
    max_dispatch: int = 1,
) -> ScanAdmitResult:
    """Claim up to ``max_dispatch`` pending scan chunks and dispatch sized Jobs."""
    cfg = dict(k8s_config or {})
    admitted = 0
    for _ in range(max(1, max_dispatch)):
        payload = queue.claim() or scanner_repo.claim_next_stranded_scan_chunk(session_factory)
        if payload is None:
            break

        prior_stats = _lookup_prior_stats(session, payload)
        estimate = estimate_scan_resources(
            prior_stats,
            ceilings=ResourceCeilings.from_config(cfg),
        )
        run_id = str(payload.get("run_id") or "") or None
        result = create_scan_job(
            {**payload, "_already_claimed": True},
            config=cfg,
            cpu_milli=estimate.cpu_milli,
            mem_mib=estimate.mem_mib,
            run_id=run_id,
        )
        if not result.ok:
            logger.error("Scan Job dispatch failed for run %s: %s", run_id, result.error)
            continue
        admitted += 1
        logger.info(
            "Dispatched scan Job %s (run=%s cpu=%dm mem=%dMi) reasons=%s",
            result.job_name,
            run_id,
            estimate.cpu_milli,
            estimate.mem_mib,
            estimate.reasons,
        )
    return ScanAdmitResult(admitted=admitted, empty=admitted == 0)


def _lookup_prior_stats(session: Session, payload: Mapping[str, Any]) -> dict[str, Any] | None:
    table_ids = payload.get("table_ids") or []
    if not table_ids:
        return None
    try:
        table_id = uuid_module.UUID(str(table_ids[0]))
    except (TypeError, ValueError):
        return None
    stats = session.get(TableStats, table_id)
    if stats is None:
        return None
    return {
        "total_files": stats.total_files,
        "manifest_count": stats.manifest_count,
        "max_files_per_partition": stats.max_files_per_partition,
        "total_partitions": stats.total_partitions,
        "total_size_gb": stats.total_size_gb,
        "delete_file_pct": stats.delete_file_pct,
    }
