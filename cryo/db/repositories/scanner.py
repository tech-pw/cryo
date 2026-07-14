from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.db.models import MaintenanceRun, ScannerConfig, Table, TableStats
from cryo.scheduler.cron import next_cron_tick, validate_cron_expression

SCANNER_CONFIG_ID = 1
ACTIVE_SCAN_STATUSES = ("accepted", "queued", "running")
TERMINAL_SCAN_STATUSES = ("succeeded", "failed", "partial_failed", "cancelled")
MAX_RECORDED_ERRORS = 25


def get_scanner_config(session: Session) -> ScannerConfig:
    config = session.get(ScannerConfig, SCANNER_CONFIG_ID)
    if config is not None:
        return config
    config = ScannerConfig(id=SCANNER_CONFIG_ID)
    session.add(config)
    session.commit()
    session.refresh(config)
    return config


def update_scanner_config(session: Session, values: dict[str, Any]) -> ScannerConfig:
    config = get_scanner_config(session)
    if "schedule_cron" in values and values["schedule_cron"] is not None:
        validate_cron_expression(str(values["schedule_cron"]), raise_error=True)
    if (
        "num_chunks" in values
        and values["num_chunks"] is not None
        and int(values["num_chunks"]) <= 0
    ):
        raise ValueError("num_chunks must be positive")
    if (
        "max_concurrent_runs" in values
        and values["max_concurrent_runs"] is not None
        and int(values["max_concurrent_runs"]) <= 0
    ):
        raise ValueError("max_concurrent_runs must be positive")

    allowed = {
        "enabled",
        "schedule_cron",
        "num_chunks",
        "full_scan_default",
        "cost_full_scan_default",
        "max_concurrent_runs",
    }
    for key, value in values.items():
        if key in allowed and value is not None:
            setattr(config, key, value)
    config.timezone = "UTC"
    session.commit()
    session.refresh(config)
    return config


def scanner_status(
    session: Session,
    *,
    queue_depth: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(UTC)
    config = get_scanner_config(session)
    latest_run = _latest_scan_run(session)
    active_run = _active_scan_run(session)
    last_successful_run = session.scalar(
        select(MaintenanceRun)
        .where(MaintenanceRun.kind == "scan", MaintenanceRun.status.in_(TERMINAL_SCAN_STATUSES))
        .order_by(MaintenanceRun.finished_at.desc().nullslast(), MaintenanceRun.created_at.desc())
        .limit(1)
    )
    total_tables = int(
        session.scalar(select(func.count(Table.id)).where(Table.is_active.is_(True))) or 0
    )
    tables_with_stats = int(
        session.scalar(
            select(func.count(Table.id))
            .select_from(Table)
            .join(TableStats)
            .where(Table.is_active.is_(True))
        )
        or 0
    )
    latest_table_scan_at = session.scalar(
        select(func.max(TableStats.scan_at)).select_from(Table).join(TableStats)
    )
    next_scan_at = (
        next_cron_tick(config.schedule_cron, now=current)
        if config.enabled and validate_cron_expression(config.schedule_cron)
        else None
    )

    return {
        "config": config,
        "queue_depth": queue_depth,
        "latest_run": latest_run,
        "active_run": active_run,
        "last_successful_run": last_successful_run,
        "next_scan_at": next_scan_at,
        "total_tables": total_tables,
        "tables_with_stats": tables_with_stats,
        "latest_table_scan_at": latest_table_scan_at,
    }


def _table_name_key(item: Any) -> str:
    """Normalize a table_names entry (dict with schema_name/table_name, or already a "a.b"
    string) into one comparable key."""
    if isinstance(item, dict):
        return f"{item.get('schema_name', '')}.{item.get('table_name', '')}".lower()
    return str(item).lower()


def active_scan_conflict(session: Session, payload: dict[str, Any]) -> str | None:
    incoming_scope = str(payload.get("scope") or "all")
    incoming_table_ids = {str(item) for item in payload.get("table_ids", [])}
    incoming_table_names = {_table_name_key(item) for item in payload.get("table_names", [])}
    incoming_schemas = {str(item) for item in payload.get("schema_names", [])}
    incoming_source = str(payload.get("source_id") or "")

    active_runs = list(
        session.scalars(
            select(MaintenanceRun)
            .where(MaintenanceRun.kind == "scan", MaintenanceRun.status.in_(ACTIVE_SCAN_STATUSES))
            .order_by(MaintenanceRun.created_at)
        )
    )
    if not active_runs:
        return None
    if incoming_scope == "all":
        return "Another scan is already active"

    for run in active_runs:
        summary = dict(run.summary or {})
        active_scope = str(summary.get("scope") or "all")
        if active_scope == "all":
            return "An all-table scan is already active"
        if incoming_scope == "tables" and active_scope == "tables":
            active_table_ids = {str(item) for item in summary.get("table_ids", [])}
            if incoming_table_ids & active_table_ids:
                return "A scan is already active for one or more selected tables"
            active_table_names = {_table_name_key(item) for item in summary.get("table_names", [])}
            if incoming_table_names & active_table_names:
                return "A scan is already active for one or more selected tables"
        if incoming_scope == "schemas" and active_scope == "schemas":
            active_schemas = {str(item) for item in summary.get("schema_names", [])}
            if incoming_schemas & active_schemas:
                return "A scan is already active for one or more selected schemas"
        if incoming_scope == "source" and active_scope == "source":
            active_source = str(summary.get("source_id") or "")
            if incoming_source and incoming_source == active_source:
                return "A scan is already active for this source"
    return None


def initialize_scan_chunks(
    session: Session,
    run_id: uuid.UUID,
    *,
    num_chunks: int,
    queued: bool,
) -> MaintenanceRun | None:
    run = session.get(MaintenanceRun, run_id)
    if run is None:
        return None
    chunks = {
        str(index): {
            "status": "queued" if queued else "accepted",
            "discovered": 0,
            "assigned": 0,
            "scanned": 0,
            "skipped": 0,
            "failed": 0,
        }
        for index in range(num_chunks)
    }
    run.status = "queued" if queued else "accepted"
    run.summary = {
        **dict(run.summary or {}),
        "queued": queued,
        "chunks_total": num_chunks,
        "chunks_pending": num_chunks if queued else 0,
        "chunks_running": 0,
        "chunks_done": 0,
        "chunks_failed": 0,
        "chunks_remaining": num_chunks if queued else 0,
        "chunks": chunks,
    }
    session.commit()
    session.refresh(run)
    return run


_CLAIMABLE_CHUNK_STATUSES = frozenset({"queued", "accepted"})


def _apply_chunk_claim(
    run: MaintenanceRun,
    *,
    chunk_id: int,
    num_chunks: int,
) -> bool:
    """Mutate a locked run to claim ``chunk_id``. Caller must hold the row lock and commit."""
    if run.status == "cancelled" or _cancel_requested(dict(run.summary or {})):
        return False
    summary = _chunk_summary(run, chunk_id=chunk_id, num_chunks=num_chunks)
    chunk = _chunk(summary, chunk_id)
    status = str(chunk.get("status") or "queued")
    if status not in _CLAIMABLE_CHUNK_STATUSES:
        return False

    now = datetime.now(UTC).isoformat()
    chunk.update(
        {
            "status": "running",
            "discovered": 0,
            "assigned": 0,
            "scanned": 0,
            "skipped": 0,
            "failed": 0,
            "started_at": now,
            "heartbeat_at": now,
        }
    )
    chunk.pop("finished_at", None)
    chunk.pop("error", None)
    chunk.pop("current_table", None)
    summary["queued"] = False
    summary.setdefault("started_at", now)
    _recompute_chunk_totals(summary)
    run.status = "running"
    run.summary = summary
    return True


def try_claim_scan_chunk(
    session_factory: Any,
    run_id: uuid.UUID,
    *,
    chunk_id: int,
    num_chunks: int,
) -> bool:
    """Atomically claim a scan chunk under row lock.

    Returns True only when this caller becomes the sole owner. Resets per-chunk
    counters so a re-queued attempt cannot accumulate on top of a previous pass.
    Refuses chunks that are already running or terminal (prevents Redis+DB
    double-claim from inflating scanned/chunk totals).
    """
    with session_factory() as session:
        run = _locked_run(session, run_id)
        if run is None:
            return False
        if not _apply_chunk_claim(run, chunk_id=chunk_id, num_chunks=num_chunks):
            return False
        session.commit()
        return True


def claim_next_stranded_scan_chunk(session_factory: Any) -> dict[str, Any] | None:
    """Atomically claim the next queued/accepted chunk from an active scan run.

    Used as the Redis-empty DB fallback. Claiming happens under ``FOR UPDATE`` so two
    idle workers cannot both observe the same queued chunk and later double-count.
    """
    with session_factory() as session:
        run_ids = list(
            session.scalars(
                select(MaintenanceRun.id)
                .where(
                    MaintenanceRun.kind == "scan",
                    MaintenanceRun.status.in_(ACTIVE_SCAN_STATUSES),
                )
                .order_by(MaintenanceRun.created_at)
                .limit(10)
            )
        )
        for run_id in run_ids:
            run = _locked_run(session, run_id)
            if run is None or run.status == "cancelled":
                continue
            summary = dict(run.summary or {})
            chunks = summary.get("chunks")
            chunk_id: int | None = None
            if isinstance(chunks, dict):
                sorted_chunks = sorted(
                    chunks.items(),
                    key=lambda item: int(item[0]) if str(item[0]).isdigit() else 10**9,
                )
                for raw_chunk_id, chunk in sorted_chunks:
                    if isinstance(chunk, dict) and chunk.get("status") in _CLAIMABLE_CHUNK_STATUSES:
                        chunk_id = int(raw_chunk_id)
                        break
            elif run.status == "accepted":
                chunk_id = 0
            if chunk_id is None:
                continue

            num_chunks = int(summary.get("chunks_total") or 1)
            if not _apply_chunk_claim(run, chunk_id=chunk_id, num_chunks=num_chunks):
                continue
            session.commit()
            summary = dict(run.summary or {})
            return {
                "run_id": str(run.id),
                "scope": summary.get("scope", "all"),
                "source_id": summary.get("source_id"),
                "table_ids": summary.get("table_ids", []),
                "table_names": summary.get("table_names", []),
                "schema_names": summary.get("schema_names", []),
                "cost_full_scan": bool(summary.get("cost_full_scan")),
                "full_scan": bool(summary.get("full_scan", True)),
                "chunk_id": chunk_id,
                "num_chunks": num_chunks,
                "trigger": run.trigger,
                "_already_claimed": True,
            }
        return None


def record_scan_chunk_started(
    session_factory: Any,
    run_id: uuid.UUID,
    *,
    chunk_id: int,
    num_chunks: int,
) -> bool:
    """Backward-compatible alias for :func:`try_claim_scan_chunk`."""
    return try_claim_scan_chunk(
        session_factory,
        run_id,
        chunk_id=chunk_id,
        num_chunks=num_chunks,
    )


def record_scan_progress(
    session_factory: Any,
    run_id: uuid.UUID,
    *,
    chunk_id: int,
    num_chunks: int,
    event: dict[str, Any],
) -> None:
    with session_factory() as session:
        run = _locked_run(session, run_id)
        if run is None:
            return
        summary = _chunk_summary(run, chunk_id=chunk_id, num_chunks=num_chunks)
        chunk = _chunk(summary, chunk_id)
        # Ignore late/duplicate workers: only the owning running claim may mutate counters.
        if chunk.get("status") != "running":
            return
        chunk["heartbeat_at"] = datetime.now(UTC).isoformat()
        event_type = str(event.get("event") or "")
        table_ref = event.get("table_ref")
        if event_type == "assigned":
            chunk["discovered"] = int(event.get("discovered") or 0)
            chunk["assigned"] = int(event.get("assigned") or 0)
            # Fresh assignment owns the outcome counters for this attempt.
            chunk["scanned"] = 0
            chunk["skipped"] = 0
            chunk["failed"] = 0
        elif event_type == "start_table":
            chunk["current_table"] = table_ref
            summary["current_table"] = table_ref
        elif event_type == "table_result":
            outcome = str(event.get("outcome") or "")
            if outcome in {"scanned", "skipped", "failed"}:
                next_count = int(chunk.get(outcome) or 0) + 1
                assigned = int(chunk.get("assigned") or 0)
                completed = (
                    int(chunk.get("scanned") or 0)
                    + int(chunk.get("skipped") or 0)
                    + int(chunk.get("failed") or 0)
                )
                # Hard cap: never let outcome totals exceed tables assigned to this chunk.
                if assigned > 0 and completed >= assigned:
                    _recompute_chunk_totals(summary)
                    run.summary = summary
                    session.commit()
                    return
                chunk[outcome] = next_count
            if outcome == "failed":
                error = str(event.get("error") or "scan failed")
                summary["last_error"] = error
                _append_failed_table(summary, table_ref=table_ref, error=error)
        _recompute_chunk_totals(summary)
        run.summary = summary
        session.commit()


def record_scan_chunk_finished(
    session_factory: Any,
    run_id: uuid.UUID,
    *,
    chunk_id: int,
    num_chunks: int,
    status: str,
    summary_update: dict[str, int],
    error: str | None = None,
) -> None:
    with session_factory() as session:
        run = _locked_run(session, run_id)
        if run is None:
            return
        summary = _chunk_summary(run, chunk_id=chunk_id, num_chunks=num_chunks)
        chunk = _chunk(summary, chunk_id)
        # Duplicate finish from a lost race must not clobber the owner's counters/status.
        if chunk.get("status") != "running":
            return

        assigned = int(summary_update.get("assigned", int(chunk.get("assigned") or 0)) or 0)
        scanned = int(summary_update.get("scanned", int(chunk.get("scanned") or 0)) or 0)
        skipped = int(summary_update.get("skipped", int(chunk.get("skipped") or 0)) or 0)
        failed = int(summary_update.get("failed", int(chunk.get("failed") or 0)) or 0)
        if assigned > 0:
            # Keep finished counters self-consistent even if a buggy worker over-reports.
            overflow = max(0, scanned + skipped + failed - assigned)
            if overflow:
                # Prefer trimming scanned first (most likely double-count path).
                trim = min(scanned, overflow)
                scanned -= trim
                overflow -= trim
                trim = min(skipped, overflow)
                skipped -= trim
                overflow -= trim
                failed = max(0, failed - overflow)

        chunk.update(
            {
                "status": status,
                "finished_at": datetime.now(UTC).isoformat(),
                "discovered": summary_update.get("discovered", int(chunk.get("discovered") or 0)),
                "assigned": assigned,
                "scanned": scanned,
                "skipped": skipped,
                "failed": failed,
            }
        )
        chunk.pop("current_table", None)
        if error:
            chunk["error"] = error
            summary["last_error"] = error
        summary["queued"] = False
        _recompute_chunk_totals(summary)

        if _cancel_requested(summary):
            run.status = "cancelled"
            run.finished_at = datetime.now(UTC)
        elif (
            int(summary.get("chunks_running") or 0) == 0
            and int(summary.get("chunks_pending") or 0) == 0
        ):
            run.status = "failed" if int(summary.get("chunks_failed") or 0) else "succeeded"
            if int(summary.get("failed") or 0) and run.status == "succeeded":
                run.status = "partial_failed"
            run.finished_at = datetime.now(UTC)
        else:
            run.status = "running"
        run.summary = summary
        session.commit()


def request_scan_cancel(session: Session, run: MaintenanceRun) -> None:
    summary = dict(run.summary or {})
    summary["cancel_requested"] = True
    summary["queued"] = False
    chunks = summary.get("chunks")
    if isinstance(chunks, dict):
        finished_at = datetime.now(UTC).isoformat()
        for chunk in chunks.values():
            if not isinstance(chunk, dict):
                continue
            if chunk.get("status") in _CLAIMABLE_CHUNK_STATUSES | {"running"}:
                chunk["status"] = "cancelled"
                chunk.setdefault("finished_at", finished_at)
                chunk.pop("current_table", None)
                chunk.pop("heartbeat_at", None)
    _recompute_chunk_totals(summary)
    run.status = "cancelled"
    run.finished_at = datetime.now(UTC)
    run.summary = summary


def scan_cancel_requested(session_factory: Any, run_id: uuid.UUID) -> bool:
    with session_factory() as session:
        run = session.get(MaintenanceRun, run_id)
        if run is None:
            return True
        return run.status == "cancelled" or _cancel_requested(dict(run.summary or {}))


def _latest_scan_run(session: Session) -> MaintenanceRun | None:
    return session.scalar(
        select(MaintenanceRun)
        .where(MaintenanceRun.kind == "scan")
        .order_by(MaintenanceRun.created_at.desc())
        .limit(1)
    )


def _active_scan_run(session: Session) -> MaintenanceRun | None:
    return session.scalar(
        select(MaintenanceRun)
        .where(MaintenanceRun.kind == "scan", MaintenanceRun.status.in_(ACTIVE_SCAN_STATUSES))
        .order_by(MaintenanceRun.created_at)
        .limit(1)
    )


def _locked_run(session: Session, run_id: uuid.UUID) -> MaintenanceRun | None:
    return session.scalar(
        select(MaintenanceRun).where(MaintenanceRun.id == run_id).with_for_update()
    )


def _chunk_summary(run: MaintenanceRun, *, chunk_id: int, num_chunks: int) -> dict[str, Any]:
    summary = dict(run.summary or {})
    chunks = dict(summary.get("chunks") or {})
    # Prefer the run's established chunk count so a stale/wrong payload cannot invent
    # extra "queued" phantom chunks (which leave chunks_pending stuck at 1 forever).
    established = int(summary.get("chunks_total") or 0)
    if established <= 0 and chunks:
        established = len(chunks)
    total = established if established > 0 else max(num_chunks, 1)
    for index in range(total):
        chunks.setdefault(
            str(index),
            {
                "status": "queued",
                "discovered": 0,
                "assigned": 0,
                "scanned": 0,
                "skipped": 0,
                "failed": 0,
            },
        )
    summary["chunks"] = chunks
    summary["chunks_total"] = total
    # Only materialize out-of-range chunk_ids as empty dicts for the caller; never grow total.
    _chunk(summary, chunk_id)
    return summary


def _chunk(summary: dict[str, Any], chunk_id: int) -> dict[str, Any]:
    chunks = summary.setdefault("chunks", {})
    chunk = chunks.setdefault(str(chunk_id), {})
    if not isinstance(chunk, dict):
        chunk = {}
        chunks[str(chunk_id)] = chunk
    return chunk


def _recompute_chunk_totals(summary: dict[str, Any]) -> None:
    chunks = summary.get("chunks") or {}
    established = int(summary.get("chunks_total") or 0)
    chunk_values: list[dict[str, Any]] = []
    for key, chunk in chunks.items():
        if not isinstance(chunk, dict):
            continue
        # Ignore accidental out-of-range keys so they cannot inflate pending/running.
        if established > 0:
            try:
                if int(key) < 0 or int(key) >= established:
                    continue
            except (TypeError, ValueError):
                continue
        chunk_values.append(chunk)
    summary["chunks_total"] = established if established > 0 else len(chunk_values)
    summary["chunks_pending"] = sum(
        1 for chunk in chunk_values if chunk.get("status") in _CLAIMABLE_CHUNK_STATUSES
    )
    summary["chunks_running"] = sum(1 for chunk in chunk_values if chunk.get("status") == "running")
    summary["chunks_done"] = sum(
        1
        for chunk in chunk_values
        if chunk.get("status") in {"succeeded", "partial_failed", "cancelled"}
    )
    summary["chunks_failed"] = sum(1 for chunk in chunk_values if chunk.get("status") == "failed")
    summary["discovered"] = max(
        (int(chunk.get("discovered") or 0) for chunk in chunk_values),
        default=0,
    )
    for key in ("assigned", "scanned", "skipped", "failed"):
        summary[key] = sum(int(chunk.get(key) or 0) for chunk in chunk_values)
    # Surface remaining work for the UI (pending + still running).
    summary["chunks_remaining"] = int(summary["chunks_pending"]) + int(summary["chunks_running"])


def _append_failed_table(
    summary: dict[str, Any],
    *,
    table_ref: object,
    error: str,
) -> None:
    failed_tables = summary.get("failed_tables")
    if not isinstance(failed_tables, list):
        failed_tables = []
    failed_tables.append(
        {
            "table": str(table_ref or "unknown"),
            "error": error,
            "ts": datetime.now(UTC).isoformat(),
        }
    )
    summary["failed_tables"] = failed_tables[-MAX_RECORDED_ERRORS:]


def _cancel_requested(summary: dict[str, Any]) -> bool:
    return bool(summary.get("cancel_requested"))


def _parse_heartbeat(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class StaleScanChunk:
    """A chunk claimed by a worker that hasn't reported progress in too long -- most likely the
    worker pod died mid-chunk (redeploy, OOM, node eviction, ...). Nothing else in the scanner
    subsystem reclaims an orphaned chunk, so without this the run just stalls forever with no
    active consumer and no visible error."""

    __slots__ = ("run_id", "chunk_id", "num_chunks", "payload", "last_heartbeat_at")

    def __init__(
        self,
        *,
        run_id: uuid.UUID,
        chunk_id: int,
        num_chunks: int,
        payload: dict[str, Any],
        last_heartbeat_at: datetime | None,
    ) -> None:
        self.run_id = run_id
        self.chunk_id = chunk_id
        self.num_chunks = num_chunks
        self.payload = payload
        self.last_heartbeat_at = last_heartbeat_at


def find_stale_running_chunks(
    session: Session,
    *,
    stale_after_seconds: float,
    now: datetime | None = None,
) -> list[StaleScanChunk]:
    """Read-only scan for chunks stuck in status='running' with no heartbeat in
    `stale_after_seconds`. Callers are expected to re-queue the returned chunks and then call
    `mark_chunk_requeued` for each -- this function only finds them."""
    current = now or datetime.now(UTC)
    threshold = current - timedelta(seconds=stale_after_seconds)
    stale: list[StaleScanChunk] = []
    runs = session.scalars(
        select(MaintenanceRun).where(
            MaintenanceRun.kind == "scan",
            MaintenanceRun.status == "running",
        )
    ).all()
    for run in runs:
        summary = dict(run.summary or {})
        chunks = summary.get("chunks") or {}
        num_chunks = int(summary.get("chunks_total") or len(chunks) or 1)
        if not isinstance(chunks, dict):
            continue
        for chunk_id_str, chunk in chunks.items():
            if not isinstance(chunk, dict) or chunk.get("status") != "running":
                continue
            heartbeat_at = _parse_heartbeat(chunk.get("heartbeat_at") or chunk.get("started_at"))
            if heartbeat_at is not None and heartbeat_at > threshold:
                continue
            try:
                chunk_id = int(chunk_id_str)
            except ValueError:
                continue
            stale.append(
                StaleScanChunk(
                    run_id=run.id,
                    chunk_id=chunk_id,
                    num_chunks=num_chunks,
                    payload={
                        "scope": summary.get("scope"),
                        "source_id": summary.get("source_id"),
                        "table_ids": summary.get("table_ids") or [],
                        "table_names": summary.get("table_names") or [],
                        "schema_names": summary.get("schema_names") or [],
                        "cost_full_scan": bool(summary.get("cost_full_scan")),
                        "full_scan": bool(summary.get("full_scan", True)),
                        "trigger": "reconcile-stale-chunk",
                    },
                    last_heartbeat_at=heartbeat_at,
                )
            )
    return stale


def mark_chunk_requeued(
    session_factory: Any,
    run_id: uuid.UUID,
    *,
    chunk_id: int,
    num_chunks: int,
) -> None:
    """Flip a stale chunk back to 'queued' after re-pushing it onto the scan queue, so the run
    summary reflects reality instead of a worker that no longer exists."""
    with session_factory() as session:
        run = _locked_run(session, run_id)
        if run is None:
            return
        summary = _chunk_summary(run, chunk_id=chunk_id, num_chunks=num_chunks)
        chunk = _chunk(summary, chunk_id)
        if chunk.get("status") != "running":
            # Someone else already reconciled or finished this chunk; don't clobber it.
            return
        # Reset counters on requeue so a retry cannot accumulate on the orphaned pass.
        chunk.update(
            {
                "status": "queued",
                "discovered": 0,
                "assigned": 0,
                "scanned": 0,
                "skipped": 0,
                "failed": 0,
            }
        )
        chunk.pop("current_table", None)
        chunk.pop("heartbeat_at", None)
        chunk.pop("finished_at", None)
        chunk.pop("error", None)
        summary["queued"] = True
        _recompute_chunk_totals(summary)
        # Run status stays "running" (its current value) -- we just found an incomplete chunk,
        # so the run is by definition not finished yet.
        run.summary = summary
        session.commit()
