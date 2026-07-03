from __future__ import annotations

import uuid
from datetime import UTC, datetime
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
        "timezone",
        "num_chunks",
        "full_scan_default",
        "cost_full_scan_default",
        "max_concurrent_runs",
    }
    for key, value in values.items():
        if key in allowed and value is not None:
            setattr(config, key, value)
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


def active_scan_conflict(session: Session, payload: dict[str, Any]) -> str | None:
    incoming_scope = str(payload.get("scope") or "all")
    incoming_table_ids = {str(item) for item in payload.get("table_ids", [])}
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
        "chunks": chunks,
    }
    session.commit()
    session.refresh(run)
    return run


def record_scan_chunk_started(
    session_factory: Any,
    run_id: uuid.UUID,
    *,
    chunk_id: int,
    num_chunks: int,
) -> None:
    with session_factory() as session:
        run = _locked_run(session, run_id)
        if run is None:
            return
        summary = _chunk_summary(run, chunk_id=chunk_id, num_chunks=num_chunks)
        chunk = _chunk(summary, chunk_id)
        chunk["status"] = "running"
        chunk.setdefault("started_at", datetime.now(UTC).isoformat())
        summary["queued"] = False
        summary.setdefault("started_at", datetime.now(UTC).isoformat())
        _recompute_chunk_totals(summary)
        run.status = "running"
        run.summary = summary
        session.commit()


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
        event_type = str(event.get("event") or "")
        table_ref = event.get("table_ref")
        if event_type == "assigned":
            chunk["discovered"] = int(event.get("discovered") or 0)
            chunk["assigned"] = int(event.get("assigned") or 0)
        elif event_type == "start_table":
            chunk["current_table"] = table_ref
            summary["current_table"] = table_ref
        elif event_type == "table_result":
            outcome = str(event.get("outcome") or "")
            if outcome in {"scanned", "skipped", "failed"}:
                chunk[outcome] = int(chunk.get(outcome) or 0) + 1
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
        chunk.update(
            {
                "status": status,
                "finished_at": datetime.now(UTC).isoformat(),
                "discovered": summary_update.get("discovered", int(chunk.get("discovered") or 0)),
                "assigned": summary_update.get("assigned", int(chunk.get("assigned") or 0)),
                "scanned": summary_update.get("scanned", int(chunk.get("scanned") or 0)),
                "skipped": summary_update.get("skipped", int(chunk.get("skipped") or 0)),
                "failed": summary_update.get("failed", int(chunk.get("failed") or 0)),
            }
        )
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
    run.status = "cancelled"
    run.finished_at = datetime.now(UTC)
    run.summary = {**dict(run.summary or {}), "cancel_requested": True, "queued": False}


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
    for index in range(num_chunks):
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
    summary["chunks_total"] = num_chunks
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
    chunk_values = [chunk for chunk in chunks.values() if isinstance(chunk, dict)]
    summary["chunks_total"] = len(chunk_values)
    summary["chunks_pending"] = sum(1 for chunk in chunk_values if chunk.get("status") == "queued")
    summary["chunks_running"] = sum(1 for chunk in chunk_values if chunk.get("status") == "running")
    summary["chunks_done"] = sum(
        1 for chunk in chunk_values if chunk.get("status") in {"succeeded", "partial_failed"}
    )
    summary["chunks_failed"] = sum(1 for chunk in chunk_values if chunk.get("status") == "failed")
    summary["discovered"] = max(
        (int(chunk.get("discovered") or 0) for chunk in chunk_values),
        default=0,
    )
    for key in ("assigned", "scanned", "skipped", "failed"):
        summary[key] = sum(int(chunk.get(key) or 0) for chunk in chunk_values)


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
