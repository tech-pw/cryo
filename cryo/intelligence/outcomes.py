from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import (
    AuditLog,
    EffectReceipt,
    MaintenanceIntent,
    MaintenanceJob,
    TableCostHistory,
    TableStatsHistory,
)

OUTCOME_SCHEMA_VERSION = "cryo.maintenance-outcome/v1"
DEFAULT_OBSERVATION_LOOKBACK_DAYS = 90
DEFAULT_POST_SCAN_WINDOW_DAYS = 7

_RECLAIM_FIELD_BY_ACTION = {
    "expire_snapshots": "reclaim_expire_bytes",
    "remove_orphan_files": "reclaim_orphan_bytes",
    "optimize": "reclaim_optimize_bytes",
    "optimize_manifests": "reclaim_manifests_bytes",
}


class _ScannedRow(Protocol):
    scan_at: datetime


@dataclass(frozen=True, slots=True)
class MaintenanceOutcome:
    audit_id: int
    job_id: uuid.UUID
    intent_id: uuid.UUID | None
    plan_hash: str | None
    table_id: uuid.UUID
    action: str
    engine: str | None
    outcome_at: datetime
    observation_at: datetime | None
    post_scan_at: datetime | None
    predicted_bytes_freed: int | None
    predicted_savings_usd_month: float | None
    actual_bytes_freed: int
    estimated_rewrite_bytes: int | None
    actual_rewrite_bytes: int | None
    duration_ms: int | None
    input_files_count: int | None
    pre_small_files_count: int | None
    post_small_files_count: int | None
    pre_total_files: int | None
    post_total_files: int | None
    pre_total_size_gb: float | None
    post_total_size_gb: float | None
    pre_delete_file_pct: float | None
    post_delete_file_pct: float | None
    pre_p95_query_latency_ms: float | None
    post_p95_query_latency_ms: float | None

    @property
    def lineage_complete(self) -> bool:
        return (
            self.intent_id is not None
            and self.plan_hash is not None
            and self.observation_at is not None
            and self.post_scan_at is not None
            and self.predicted_bytes_freed is not None
        )

    def calibration_row(self) -> dict[str, Any] | None:
        if not self.lineage_complete or self.predicted_bytes_freed is None:
            return None
        return {
            "schema_version": OUTCOME_SCHEMA_VERSION,
            "audit_id": self.audit_id,
            "job_id": str(self.job_id),
            "intent_id": str(self.intent_id),
            "plan_hash": self.plan_hash,
            "action": self.action,
            "engine": self.engine,
            "observed_at": self.observation_at.isoformat() if self.observation_at else None,
            "outcome_at": self.outcome_at.isoformat(),
            "post_scan_at": self.post_scan_at.isoformat() if self.post_scan_at else None,
            "predicted_bytes_freed": self.predicted_bytes_freed,
            "actual_bytes_freed": self.actual_bytes_freed,
            "estimated_rewrite_bytes": self.estimated_rewrite_bytes,
            "actual_rewrite_bytes": self.actual_rewrite_bytes,
            "duration_ms": self.duration_ms,
            "predicted_savings_usd_month": self.predicted_savings_usd_month,
            "small_files_count": self.pre_small_files_count or 0,
            "total_size_gb": self.pre_total_size_gb or 0.0,
            "delete_file_pct": self.pre_delete_file_pct or 0.0,
            "input_files_count": self.input_files_count or 0,
            "post_small_files_count": self.post_small_files_count,
            "post_total_files": self.post_total_files,
            "post_total_size_gb": self.post_total_size_gb,
            "pre_delete_file_pct": self.pre_delete_file_pct,
            "post_delete_file_pct": self.post_delete_file_pct,
            "pre_p95_query_latency_ms": self.pre_p95_query_latency_ms,
            "post_p95_query_latency_ms": self.post_p95_query_latency_ms,
        }


@dataclass(frozen=True, slots=True)
class OutcomeDataset:
    rows: tuple[MaintenanceOutcome, ...]

    @property
    def eligible_count(self) -> int:
        return len(self.rows)

    @property
    def complete_count(self) -> int:
        return sum(row.lineage_complete for row in self.rows)

    @property
    def completeness(self) -> float:
        if not self.rows:
            return 0.0
        return round(self.complete_count / len(self.rows), 4)

    def calibration_rows(self) -> list[dict[str, Any]]:
        return [
            calibration for row in self.rows if (calibration := row.calibration_row()) is not None
        ]


def build_outcome_dataset(
    session: Session,
    *,
    table_id: uuid.UUID | None = None,
    limit: int = 500,
    observation_lookback_days: int = DEFAULT_OBSERVATION_LOOKBACK_DAYS,
    post_scan_window_days: int = DEFAULT_POST_SCAN_WINDOW_DAYS,
) -> OutcomeDataset:
    if limit < 1:
        raise ValueError("outcome dataset limit must be positive")
    if observation_lookback_days < 1:
        raise ValueError("observation lookback must be positive")
    if post_scan_window_days < 1:
        raise ValueError("post-scan window must be positive")
    query = (
        select(AuditLog, MaintenanceJob)
        .join(MaintenanceJob, MaintenanceJob.id == AuditLog.job_id)
        .where(
            AuditLog.status == "OK",
            AuditLog.table_id.isnot(None),
            AuditLog.actual_bytes_freed.isnot(None),
        )
    )
    if table_id is not None:
        query = query.where(AuditLog.table_id == table_id)
    audit_jobs = list(
        session.execute(query.order_by(AuditLog.ts.desc(), AuditLog.id.desc()).limit(limit))
    )
    if not audit_jobs:
        return OutcomeDataset(())

    table_ids = {audit.table_id for audit, _ in audit_jobs if audit.table_id is not None}
    earliest_outcome = min(audit.ts for audit, _ in audit_jobs)
    latest_outcome = max(audit.ts for audit, _ in audit_jobs)
    history_start = earliest_outcome - timedelta(days=observation_lookback_days)
    history_end = latest_outcome + timedelta(days=post_scan_window_days)
    stats_by_table: dict[uuid.UUID, list[TableStatsHistory]] = {
        table_id: [] for table_id in table_ids
    }
    for stats_history in session.scalars(
        select(TableStatsHistory)
        .where(
            TableStatsHistory.table_id.in_(table_ids),
            TableStatsHistory.scan_at >= history_start,
            TableStatsHistory.scan_at <= history_end,
        )
        .order_by(TableStatsHistory.table_id, TableStatsHistory.scan_at)
    ):
        stats_by_table[stats_history.table_id].append(stats_history)

    costs_by_table: dict[uuid.UUID, list[TableCostHistory]] = {
        table_id: [] for table_id in table_ids
    }
    for cost_history in session.scalars(
        select(TableCostHistory)
        .where(
            TableCostHistory.table_id.in_(table_ids),
            TableCostHistory.scan_at >= history_start,
            TableCostHistory.scan_at <= latest_outcome,
        )
        .order_by(TableCostHistory.table_id, TableCostHistory.scan_at)
    ):
        costs_by_table[cost_history.table_id].append(cost_history)

    intent_ids = {
        job.current_intent_id for _, job in audit_jobs if job.current_intent_id is not None
    }
    intents = (
        {
            intent.id: intent
            for intent in session.scalars(
                select(MaintenanceIntent).where(MaintenanceIntent.id.in_(intent_ids))
            )
        }
        if intent_ids
        else {}
    )
    rewrite_bytes_by_intent_operation: dict[tuple[uuid.UUID, str], int] = {}
    if intent_ids:
        for receipt in session.scalars(
            select(EffectReceipt).where(
                EffectReceipt.intent_id.in_(intent_ids),
                EffectReceipt.state == "succeeded",
            )
        ):
            actual_rewrite_bytes = _result_nonnegative_int(
                receipt.result,
                "actual_bytes_written",
            )
            if actual_rewrite_bytes is not None:
                rewrite_bytes_by_intent_operation[(receipt.intent_id, receipt.operation_type)] = (
                    actual_rewrite_bytes
                )

    outcomes: list[MaintenanceOutcome] = []
    for audit, job in reversed(audit_jobs):
        if audit.table_id is None or audit.actual_bytes_freed is None:
            continue
        histories = stats_by_table.get(audit.table_id, [])
        pre_stats = _latest_at_or_before(histories, audit.ts)
        post_stats = _first_after(histories, audit.ts)
        pre_cost = _latest_at_or_before(costs_by_table.get(audit.table_id, []), audit.ts)
        intent = intents.get(job.current_intent_id)
        predicted_bytes = _predicted_bytes(audit.action, pre_stats, pre_cost)
        outcomes.append(
            MaintenanceOutcome(
                audit_id=audit.id,
                job_id=job.id,
                intent_id=intent.id if intent is not None else None,
                plan_hash=intent.plan_hash if intent is not None else job.plan_hash,
                table_id=audit.table_id,
                action=audit.action,
                engine=audit.engine,
                outcome_at=audit.ts,
                observation_at=pre_stats.scan_at if pre_stats is not None else None,
                post_scan_at=post_stats.scan_at if post_stats is not None else None,
                predicted_bytes_freed=predicted_bytes,
                predicted_savings_usd_month=(
                    pre_cost.projected_savings_usd_month if pre_cost is not None else None
                ),
                actual_bytes_freed=audit.actual_bytes_freed,
                estimated_rewrite_bytes=_estimated_rewrite_bytes(pre_stats),
                actual_rewrite_bytes=(
                    rewrite_bytes_by_intent_operation.get((intent.id, audit.action))
                    if intent is not None
                    else None
                ),
                duration_ms=audit.duration_ms,
                input_files_count=audit.input_files_count,
                pre_small_files_count=(
                    pre_stats.small_files_count if pre_stats is not None else None
                ),
                post_small_files_count=(
                    post_stats.small_files_count if post_stats is not None else None
                ),
                pre_total_files=pre_stats.total_files if pre_stats is not None else None,
                post_total_files=post_stats.total_files if post_stats is not None else None,
                pre_total_size_gb=pre_stats.total_size_gb if pre_stats is not None else None,
                post_total_size_gb=post_stats.total_size_gb if post_stats is not None else None,
                pre_delete_file_pct=_raw_float(pre_stats, "delete_file_pct"),
                post_delete_file_pct=_raw_float(post_stats, "delete_file_pct"),
                pre_p95_query_latency_ms=_raw_float(pre_stats, "p95_query_latency_ms"),
                post_p95_query_latency_ms=_raw_float(post_stats, "p95_query_latency_ms"),
            )
        )
    return OutcomeDataset(tuple(outcomes))


def _latest_at_or_before[T: _ScannedRow](
    rows: list[T],
    cutoff: datetime,
) -> T | None:
    return next(
        (row for row in reversed(rows) if row.scan_at <= cutoff),
        None,
    )


def _first_after[T: _ScannedRow](rows: list[T], cutoff: datetime) -> T | None:
    return next((row for row in rows if row.scan_at > cutoff), None)


def _predicted_bytes(
    action: str,
    stats: TableStatsHistory | None,
    cost: TableCostHistory | None,
) -> int | None:
    field = _RECLAIM_FIELD_BY_ACTION.get(action)
    if stats is not None and field is not None and isinstance(stats.raw, dict):
        value = stats.raw.get(field)
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
    if cost is not None and cost.reclaimable_bytes is not None and cost.reclaimable_bytes >= 0:
        return int(cost.reclaimable_bytes)
    return None


def _raw_float(row: TableStatsHistory | None, key: str) -> float | None:
    if row is None or not isinstance(row.raw, dict):
        return None
    value = row.raw.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _estimated_rewrite_bytes(row: TableStatsHistory | None) -> int | None:
    if row is None or row.total_size_gb is None or row.total_size_gb <= 0:
        return None
    return int(row.total_size_gb * 1024**3)


def _result_nonnegative_int(result: object, key: str) -> int | None:
    if not isinstance(result, dict):
        return None
    value = result.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return int(value)
