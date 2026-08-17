from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.db.models import (
    AuditLog,
    EffectReceipt,
    MaintenanceIntent,
    MaintenanceJob,
    QueryCostAttributionDaily,
    QueryLog,
    TableCostHistory,
    TableStatsHistory,
)

OUTCOME_SCHEMA_VERSION = "cryo.maintenance-outcome/v1"
DEFAULT_OBSERVATION_LOOKBACK_DAYS = 90
DEFAULT_POST_SCAN_WINDOW_DAYS = 7
_GIB = 1_073_741_824
# Trailing window for _table_scan_cost_usd, matching persist_table_analysis's
# _P95_LATENCY_LOOKBACK_DAYS convention -- consistent with the rest of the codebase's
# per-table query-signal windows rather than inventing a fourth window length.
_COST_WINDOW_DAYS = 7

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
    pre_estimated_cost_usd: float | None
    post_estimated_cost_usd: float | None

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
            "pre_estimated_cost_usd": self.pre_estimated_cost_usd,
            "post_estimated_cost_usd": self.post_estimated_cost_usd,
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

    # Real Trino $/GiB per day, backed out from QueryCostAttributionDaily's already-derived
    # estimated_scan_cost_usd (see cryo.cost.model.derive_trino_scan_cost_prices -- that
    # module produces the price, cryo.telemetry.cost_attribution.apply_scan_cost_prices writes
    # it proportionally onto every (day, client, schema) row for that day, so any row's own
    # cost/bytes ratio recovers the same day-level $/GiB rate). Coarse cost_attribution rollup
    # has no table_id -- this is why _table_scan_cost_usd re-derives per-table cost from
    # QueryLog.bytes_read directly rather than reading a per-table cost column that doesn't
    # exist anywhere in the schema.
    price_usd_per_gib_by_day: dict[Any, float] = {}
    for row in session.execute(
        select(
            QueryCostAttributionDaily.day,
            func.sum(QueryCostAttributionDaily.estimated_scan_cost_usd),
            func.sum(QueryCostAttributionDaily.physical_bytes_scanned),
        )
        .where(
            QueryCostAttributionDaily.day >= history_start.date(),
            QueryCostAttributionDaily.day <= history_end.date(),
            QueryCostAttributionDaily.estimated_scan_cost_usd.isnot(None),
        )
        .group_by(QueryCostAttributionDaily.day)
    ):
        day, cost_sum, bytes_sum = row
        if cost_sum is not None and bytes_sum:
            price_usd_per_gib_by_day[day] = float(cost_sum) / (float(bytes_sum) / _GIB)

    bytes_read_by_table_day: dict[tuple[uuid.UUID, Any], int] = {}
    for row in session.execute(
        select(
            QueryLog.table_id,
            func.date(QueryLog.executed_at),
            func.sum(QueryLog.bytes_read),
        )
        .where(
            QueryLog.table_id.in_(table_ids),
            QueryLog.executed_at >= history_start,
            QueryLog.executed_at <= history_end,
            QueryLog.bytes_read.isnot(None),
        )
        .group_by(QueryLog.table_id, func.date(QueryLog.executed_at))
    ):
        log_table_id, day, bytes_sum = row
        if bytes_sum:
            bytes_read_by_table_day[(log_table_id, day)] = int(bytes_sum)

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
                pre_estimated_cost_usd=_table_scan_cost_usd(
                    audit.table_id,
                    as_of=pre_stats.scan_at if pre_stats is not None else None,
                    bytes_read_by_table_day=bytes_read_by_table_day,
                    price_usd_per_gib_by_day=price_usd_per_gib_by_day,
                ),
                post_estimated_cost_usd=_table_scan_cost_usd(
                    audit.table_id,
                    as_of=post_stats.scan_at if post_stats is not None else None,
                    bytes_read_by_table_day=bytes_read_by_table_day,
                    price_usd_per_gib_by_day=price_usd_per_gib_by_day,
                ),
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


def _table_scan_cost_usd(
    table_id: uuid.UUID,
    *,
    as_of: datetime | None,
    bytes_read_by_table_day: Mapping[tuple[uuid.UUID, Any], int],
    price_usd_per_gib_by_day: Mapping[Any, float],
) -> float | None:
    """Real estimated Trino scan cost for this table over the ``_COST_WINDOW_DAYS`` trailing
    ``as_of`` -- reconstructed from QueryLog.bytes_read (per-table, real) times that day's
    derived $/GiB price (per-day, real, backed out of QueryCostAttributionDaily -- see the
    price_usd_per_gib_by_day comment at its build site in build_outcome_dataset for why this
    reconstruction is necessary rather than reading a per-table cost column, which doesn't
    exist anywhere in the schema).

    Returns None when ``as_of`` is None (no matching pre/post scan) or when any day in the
    window is missing a price (Settings.trino_cost_pricing_enabled off, or that day just
    hasn't been priced yet) -- a partial-window cost estimate would be misleadingly precise,
    not honestly partial, so this abstains rather than silently under-counting.
    """
    if as_of is None:
        return None
    total_usd = 0.0
    for offset in range(_COST_WINDOW_DAYS):
        day = (as_of - timedelta(days=offset)).date()
        price = price_usd_per_gib_by_day.get(day)
        if price is None:
            continue
        bytes_read = bytes_read_by_table_day.get((table_id, day))
        if not bytes_read:
            continue
        total_usd += (bytes_read / _GIB) * price
    return round(total_usd, 6) if total_usd > 0 else None


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
