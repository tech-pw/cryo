from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Float, desc, func, select
from sqlalchemy.orm import Session, selectinload

from cryo.db.model_utils import catalog_name_for_table, qualified_table_name
from cryo.db.models import (
    AuditLog,
    Destination,
    MaintenanceIntent,
    MaintenanceJob,
    MaintenanceRun,
    Source,
    Table,
    TableAssignment,
    TablePartition,
)
from cryo.domain.enums import JobStatus, Mode, OperationType
from cryo.observability.correlation import correlation_id_from_summary
from cryo.worker.worker import (
    AuditRecord,
    DestinationConfig,
    MaintenanceJobRecord,
    TableMaintenanceData,
)


class SQLJobStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def get_job(self, job_id: str) -> MaintenanceJobRecord:
        with self._session_factory() as session:
            job = _load_job(session, uuid.UUID(job_id))
            return _job_record(session, job)

    def get_destination(self, destination_id: str) -> DestinationConfig:
        destination = self.try_get_destination(destination_id)
        if destination is None:
            raise KeyError(f"destination not found: {destination_id}")
        return destination

    def try_get_destination(self, destination_id: str) -> DestinationConfig | None:
        with self._session_factory() as session:
            destination = session.get(Destination, uuid.UUID(destination_id))
            if destination is None:
                return None
            return _destination_config(destination)

    def get_table_data(self, table_id: str) -> TableMaintenanceData:
        with self._session_factory() as session:
            table = session.scalar(
                select(Table)
                .where(Table.id == uuid.UUID(table_id))
                .options(selectinload(Table.stats), selectinload(Table.source))
            )
            if table is None:
                raise KeyError(f"table not found: {table_id}")
            stats = _stats_dict(table)
            stats["delete_minor_runs_since_major"] = _minor_delete_runs_since_major(
                session,
                table.id,
            )
            partitions = _latest_partitions(session, table.id)
            source_config = dict(table.source.config) if table.source is not None else {}
            return TableMaintenanceData(
                stats=stats,
                partitions=partitions,
                source_config=source_config,
            )

    def save_plan(self, job_id: str, plan: dict[str, Any]) -> None:
        with self._session_factory() as session:
            job = _load_job(session, uuid.UUID(job_id))
            if job.current_intent_id is not None and job.plan != plan:
                raise RuntimeError("cannot replace the plan of a job bound to a durable intent")
            job.plan = plan
            session.commit()

    def mark_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        engine_ref: str | None = None,
        replica: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._session_factory() as session:
            job = _load_job(session, uuid.UUID(job_id))
            job.status = JobStatus(status).value
            now = datetime.now(UTC)
            if status is JobStatus.QUEUED:
                job.started_at = None
                job.finished_at = None
                job.admitted_at = None
            if status in {JobStatus.QUEUED, JobStatus.ADMITTED, JobStatus.RUNNING}:
                job.finished_at = None
            if status is JobStatus.ADMITTED:
                job.admitted_at = now
            if status is JobStatus.RUNNING and job.started_at is None:
                job.started_at = now
            if status in {
                JobStatus.SUCCEEDED,
                JobStatus.FAILED,
                JobStatus.SKIPPED,
                JobStatus.DEFERRED,
                JobStatus.PARTIAL,
            }:
                job.finished_at = now
            if engine_ref is not None:
                job.engine_ref = engine_ref
            if replica is not None:
                job.replica = replica
            job.error = error
            session.commit()

    def deactivate_table(self, table_id: str, reason: str) -> None:
        """Mark a table inactive after a maintenance job discovers it no longer exists in the
        catalog (dropped externally, out from under Cryo's own metadata). Without this, a
        dropped table stays `is_active=true` forever -- nothing else reconciles Cryo's
        inventory against real catalog state -- so scans and maintenance keep targeting it
        indefinitely. Confirmed live, 2026-07-31."""
        with self._session_factory() as session:
            table = session.get(Table, uuid.UUID(table_id))
            if table is None:
                return
            table.is_active = False
            table.last_scan_error = reason
            table.last_scan_error_at = datetime.now(UTC)
            session.commit()

    def set_force_admit(self, job_id: str, value: bool) -> None:
        with self._session_factory() as session:
            job = _load_job(session, uuid.UUID(job_id))
            job.force_admit = value
            session.commit()

    def set_engine_ref(self, job_id: str, engine_ref: str) -> None:
        with self._session_factory() as session:
            job = _load_job(session, uuid.UUID(job_id))
            job.engine_ref = engine_ref
            session.commit()

    def record_audit(self, record: AuditRecord) -> None:
        with self._session_factory() as session:
            session.add(
                AuditLog(
                    table_id=uuid.UUID(record.table_id),
                    job_id=uuid.UUID(record.job_id),
                    action=record.action.value,
                    status=record.status,
                    engine=str(record.engine),
                    detail=record.detail,
                    duration_ms=record.duration_ms,
                    actual_bytes_freed=record.actual_bytes_freed,
                    input_files_count=record.input_files_count,
                    output_files_count=record.output_files_count,
                    actor=record.actor,
                    engine_ref=record.engine_ref,
                )
            )
            if record.status == "OK":
                from cryo.workflows.approvals import (
                    ApprovalRequest,
                    ApprovalStatus,
                    requires_approval,
                )

                if requires_approval(record.action):
                    stmt = (
                        select(ApprovalRequest)
                        .where(
                            ApprovalRequest.table_id == uuid.UUID(record.table_id),
                            ApprovalRequest.operation_type == record.action.value,
                            ApprovalRequest.status == ApprovalStatus.APPROVED,
                            ApprovalRequest.executed_at.is_(None),
                        )
                        .order_by(ApprovalRequest.created_at.asc())
                        .limit(1)
                    )
                    req = session.scalar(stmt)
                    if req:
                        req.executed_at = datetime.now(UTC)
                        req.execution_job_id = uuid.UUID(record.job_id)
            session.commit()

    def pending_jobs(self, destination_id: str) -> list[MaintenanceJobRecord]:
        with self._session_factory() as session:
            jobs = session.scalars(
                select(MaintenanceJob)
                .where(
                    MaintenanceJob.destination_id == uuid.UUID(destination_id),
                    MaintenanceJob.status == JobStatus.QUEUED.value,
                )
                .options(selectinload(MaintenanceJob.run).selectinload(MaintenanceRun.preset))
            )
            return [_job_record(session, job) for job in jobs]

    def list_queued_jobs(self, *, limit: int | None = None) -> list[MaintenanceJobRecord]:
        """Live-verified 2026-09-02, same incident as list_deferred_jobs's own docstring:
        reconcile_queued_jobs (queue/reconcile.py) called this unbounded on every governor
        wave -- with 28,445 queued jobs at the time (far exceeding the 4,227 deferred that
        first surfaced the bug), this was the dominant cost once list_deferred_jobs's own
        unbounded scan was fixed: a governor pod restarted onto the fixed reconcile_deferred_
        jobs code still failed to complete a single wave for 2.5+ minutes, because this
        sibling function has the identical unbounded pattern and, at this volume, was now the
        larger bottleneck. Same fix: an optional limit, oldest-queued-first (ordered by
        created_at via the joined run, since MaintenanceJob has no created_at of its own --
        a job is created at the same time as its parent run) so a wave's own runtime stays
        bounded regardless of how deep the queued backlog grows.
        """
        with self._session_factory() as session:
            query = (
                select(MaintenanceJob)
                .where(MaintenanceJob.status == JobStatus.QUEUED.value)
                .options(selectinload(MaintenanceJob.run).selectinload(MaintenanceRun.preset))
            )
            if limit is not None:
                query = (
                    query.join(MaintenanceRun, MaintenanceRun.id == MaintenanceJob.run_id)
                    .order_by(MaintenanceRun.created_at.asc())
                    .limit(limit)
                )
            jobs = session.scalars(query)
            return [_job_record(session, job) for job in jobs]

    def list_deferred_jobs(self, *, limit: int | None = None) -> list[MaintenanceJobRecord]:
        """Live-verified 2026-09-02: reconcile_deferred_jobs (queue/reconcile.py) calls this
        unbounded on every governor wave (every 30s) -- with 4,227 deferred jobs and growing,
        each wave's reconcile step took long enough to stall the governor entirely (admitted-
        per-wave declined 15->8->7->5->5->5->2->2 across consecutive waves, then no wave
        completed at all for 3+ minutes against a 30s interval). A pod restart didn't help --
        the backlog itself, not pod state, was the bottleneck, confirming this as the root
        cause rather than a one-off hang.

        limit, when given, returns the oldest-deferred-first slice (ordered by finished_at,
        the timestamp mark_status sets when a job transitions to DEFERRED) instead of the
        whole table -- same fairness principle as fifo_priority (scheduler.py): the jobs
        that have been waiting longest get evaluated first, and a wave's own runtime stays
        bounded by limit regardless of how large the deferred backlog grows.
        """
        with self._session_factory() as session:
            query = (
                select(MaintenanceJob)
                .where(MaintenanceJob.status == JobStatus.DEFERRED.value)
                .options(selectinload(MaintenanceJob.run).selectinload(MaintenanceRun.preset))
            )
            if limit is not None:
                query = query.order_by(MaintenanceJob.finished_at.asc()).limit(limit)
            jobs = session.scalars(query)
            return [_job_record(session, job) for job in jobs]

    def list_running_jobs(self, *, limit: int | None = None) -> list[MaintenanceJobRecord]:
        """Live outage, 2026-09-10: cryo/worker/orphan_recovery.py's recover_orphaned_jobs
        calls this unconditionally on every worker pod's 5s tick, with no limit -- the exact
        same unbounded-scan pattern list_queued_jobs' own docstring already documents
        (28,445 rows, 2026-09-02) and that fix only capped the queued-jobs sibling call, not
        this one. RUNNING here includes every engine's jobs, not just the Spark/Trino ones
        orphan-recovery can actually act on -- native-engine jobs go straight to RUNNING via
        their own dispatch_id and don't need a worker to recover them (see
        _is_native_watcher_owned), so under a large cron burst this materializes far more
        full MaintenanceJobRecord objects (each eager-loading run+preset) than orphan-
        recovery has any use for. Confirmed live: multiple cryo-worker pods OOMKilled
        (linear ~1GB/6s heap growth, kernel-verified via /proc smaps) during exactly this
        recovery tick. Same fix as list_queued_jobs: an optional limit, oldest-running-first
        (ordered by started_at) so a tick's own memory footprint stays bounded regardless of
        how large the fleet-wide RUNNING set grows.
        """
        with self._session_factory() as session:
            query = (
                select(MaintenanceJob)
                .where(MaintenanceJob.status == JobStatus.RUNNING.value)
                .options(selectinload(MaintenanceJob.run).selectinload(MaintenanceRun.preset))
            )
            if limit is not None:
                query = query.order_by(MaintenanceJob.started_at.asc()).limit(limit)
            jobs = session.scalars(query)
            return [_job_record(session, job) for job in jobs]

    def list_admitted_jobs(self, *, limit: int | None = None) -> list[MaintenanceJobRecord]:
        """For reconcile_stuck_admitted_jobs -- see MaintenanceJob.admitted_at's docstring for
        the at-most-once admit-handoff bug this exists to detect and recover from.

        limit, when given, bounds the scan the same way list_running_jobs/list_queued_jobs
        do -- see list_running_jobs' docstring for the 2026-09-10 incident this closes on
        recover_orphaned_jobs' call site specifically."""
        with self._session_factory() as session:
            query = (
                select(MaintenanceJob)
                .where(MaintenanceJob.status == JobStatus.ADMITTED.value)
                .options(selectinload(MaintenanceJob.run).selectinload(MaintenanceRun.preset))
            )
            if limit is not None:
                query = (
                    query.join(MaintenanceRun, MaintenanceRun.id == MaintenanceJob.run_id)
                    .order_by(MaintenanceRun.created_at.asc())
                    .limit(limit)
                )
            jobs = session.scalars(query)
            return [_job_record(session, job) for job in jobs]

    def count_running_jobs_by_metastore_uri(self) -> dict[str, int]:
        """Count RUNNING jobs grouped by their table's source metastore URI.

        Built for a global (cross-destination) concurrency guardrail, 2026-08-12:
        native_datafusion's own slot_cap (e.g. 80) has no relationship to the shared Hive
        metastore's own connection capacity -- confirmed live the same night, a metastore
        OOM (322 jobs started in the surrounding 15 minutes, ~70 concurrently failing with
        literal "failed to connect to metastore") followed directly from the async
        fallback-dispatch throughput fix raising admission speed without anyone checking
        whether the downstream metastore had headroom for the resulting concurrency. A
        single GROUP BY query (not per-job N+1 lookups) so this is cheap enough to call
        every wave.
        """
        with self._session_factory() as session:
            uri_expr = Source.config["uri"].astext
            results = session.execute(
                select(uri_expr.label("uri"), func.count(MaintenanceJob.id))
                .select_from(MaintenanceJob)
                .join(Table, Table.id == MaintenanceJob.table_id)
                .join(Source, Source.id == Table.source_id)
                .where(MaintenanceJob.status == JobStatus.RUNNING.value)
                .group_by(uri_expr)
            ).all()
        return {uri: count for uri, count in results if uri}

    def count_jobs_started_since(self, cutoff: datetime) -> int:
        """Count all jobs (any status, any destination) that started at or after ``cutoff``.

        Fleet-wide circuit breaker, 2026-08-12: the metastore-concurrency cap
        (count_running_jobs_by_metastore_uri) bounds one specific downstream --
        it wouldn't have caught a different bottleneck the same admission burst could
        hit. Real incident that night: 100+ jobs admitted in single-minute windows,
        11,824 native jobs total across the day, 31% of them failing -- real compute
        spent and billed regardless of *why* they failed. This is a blunt, mechanism-
        agnostic ceiling on fleet-wide admission rate itself, independent of which
        specific downstream would choke on it.
        """
        with self._session_factory() as session:
            count = session.scalar(
                select(func.count(MaintenanceJob.id)).where(MaintenanceJob.started_at >= cutoff)
            )
        return int(count or 0)

    def destination_exists(self, destination_id: str) -> bool:
        with self._session_factory() as session:
            destination = session.get(Destination, uuid.UUID(destination_id))
            return destination is not None and destination.enabled


def _destination_config(destination: Destination) -> DestinationConfig:
    config = dict(destination.config)
    if "offpeak_start_ist" not in config and destination.offpeak_start is not None:
        config["offpeak_start_ist"] = destination.offpeak_start
    if "offpeak_end_ist" not in config and destination.offpeak_end is not None:
        config["offpeak_end_ist"] = destination.offpeak_end
    return DestinationConfig(
        id=str(destination.id),
        engine=destination.engine,
        config=config,
    )


def _load_job(session: Session, job_id: uuid.UUID) -> MaintenanceJob:
    job = session.scalar(
        select(MaintenanceJob)
        .where(MaintenanceJob.id == job_id)
        .options(selectinload(MaintenanceJob.run).selectinload(MaintenanceRun.preset))
    )
    if job is None:
        raise KeyError(f"job not found: {job_id}")
    return job


def _job_record(session: Session, job: MaintenanceJob) -> MaintenanceJobRecord:
    preset = job.run.preset if job.run else None
    params: dict[str, Any] = dict(preset.params) if preset else {}
    # Per-table tweaks (e.g. a tighter retention_days for one hot table) layered on top
    # of the shared preset -- without this, changing a param for one table means either
    # forking the preset or editing it in place and affecting every other table pinned
    # to it. table_assignments.param_overrides wins over the preset default.
    assignment = session.get(TableAssignment, job.table_id)
    if assignment is not None and assignment.param_overrides:
        params.update(assignment.param_overrides)
    guardrails = dict(preset.guardrails) if preset else {}
    operations = _coerce_operations(preset.operations) if preset else None
    summary = dict(job.run.summary or {}) if job.run else {}
    actor_raw = summary.get("actor")
    actor = actor_raw if isinstance(actor_raw, str) and actor_raw else None
    # AutoComp (SIGMOD 2025): governor.decide_job_admission's min_benefit_score/min_moop_score
    # gates read AdmissionJob.estimated_benefit_score/moop_score, which _admission_job builds
    # from these params -- without this, params only ever carries static preset config (never
    # a live per-table signal), so those gates never actually fire in production. setdefault
    # so an operator who genuinely configured a static value on the preset still wins.
    autocomp = _table_autocomp_summary(session, job.table_id)
    if autocomp is not None:
        params.setdefault("benefit_score", autocomp["benefit_score"])
        if autocomp["moop_score"] is not None:
            params.setdefault("moop_score", autocomp["moop_score"])
    intent = (
        session.get(MaintenanceIntent, job.current_intent_id)
        if job.current_intent_id is not None
        else None
    )
    return MaintenanceJobRecord(
        id=str(job.id),
        run_id=str(job.run_id),
        table_id=str(job.table_id),
        destination_id=str(job.destination_id),
        mode=Mode(job.mode),
        params=params,
        status=JobStatus(job.status),
        plan=job.plan,
        engine_ref=job.engine_ref,
        replica=job.replica,
        admitted_at=job.admitted_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error=job.error,
        deadline_at=job.run.deadline_at if job.run else None,
        cooldown_hours=int(guardrails.get("cooldown_hours") or 0),
        last_maintained_at=_last_maintained_at(session, job.table_id),
        actor=actor,
        operations=operations,
        force_admit=bool(job.force_admit),
        dispatch_id=str(job.dispatch_id) if job.dispatch_id is not None else None,
        intent_id=str(intent.id) if intent is not None else None,
        intent_status=intent.status if intent is not None else None,
        intent_expires_at=intent.expires_at if intent is not None else None,
        fencing_epoch=intent.fencing_epoch if intent is not None else None,
        tenant_id=(
            str(intent.tenant_id)
            if intent is not None and intent.tenant_id is not None
            else (
                str(job.table.tenant_id)
                if job.table is not None and job.table.tenant_id is not None
                else None
            )
        ),
        intent_effect_destination_ids=_intent_effect_destination_ids(intent),
        correlation_id=correlation_id_from_summary(summary),
        run_status=job.run.status if job.run else None,
    )


def _intent_effect_destination_ids(intent: MaintenanceIntent | None) -> tuple[str, ...]:
    if intent is None:
        return ()
    destination_ids: list[str] = []
    for requirement in intent.capability_requirements or []:
        if not isinstance(requirement, dict) or requirement.get("executes_effect", True) is False:
            continue
        destination_id = requirement.get("destination_id")
        if destination_id is None:
            continue
        value = str(destination_id)
        if value not in destination_ids:
            destination_ids.append(value)
    return tuple(destination_ids)


def _coerce_operations(raw: Any) -> tuple[OperationType, ...] | None:
    """Convert a preset's stored operations list into OperationType values.

    Returns None when the preset does not declare an operations list so callers can
    distinguish "not configured" from "configured as empty". Unknown values are dropped.

    The result is sorted into the canonical maintenance order -- the worker iterates these
    in sequence, so a preset row written before the ordering backfill (or hand-edited since)
    must not dictate a stale execution order.
    """
    from cryo.planner.guardrails import sort_operations_canonically

    if not isinstance(raw, (list, tuple)):
        return None
    operations: list[OperationType] = []
    for value in raw:
        try:
            operations.append(OperationType(value))
        except ValueError:
            continue
    return tuple(sort_operations_canonically(operations))


def _last_maintained_at(session: Session, table_id: uuid.UUID) -> datetime | None:
    row = session.scalar(
        select(AuditLog.ts)
        .where(AuditLog.table_id == table_id, AuditLog.status == "OK")
        .order_by(desc(AuditLog.ts))
        .limit(1)
    )
    return row


def _stats_dict(table: Table) -> dict[str, Any]:
    stats = table.stats
    catalog = catalog_name_for_table(table) if table.source is not None else "iceberg"
    table_ref = (
        qualified_table_name(table)
        if table.source is not None
        else f"{catalog}.{table.schema_name}.{table.table_name}"
    )
    base = {
        "catalog": catalog,
        "schema_name": table.schema_name,
        "table_name": table.table_name,
        "table_ref": table_ref,
        "source_id": str(table.source_id) if table.source_id is not None else None,
    }
    if stats is None:
        return base
    return {
        **base,
        "needs_compaction": stats.needs_compaction,
        "compaction_priority": stats.compaction_priority,
        "grade": stats.grade,
        "overall_score": stats.overall_score,
        "total_files": stats.total_files,
        "small_files_count": stats.small_files_count,
        "total_partitions": stats.total_partitions,
        "total_size_gb": stats.total_size_gb,
        "total_delete_files": stats.total_delete_files,
        "snapshot_watermark": stats.snapshot_watermark,
        # Okolnychyi (VLDB'24) minor/major delete policy needs these two to distinguish
        # "many small position-delete files, cheap minor compaction fixes it" from "delete
        # volume is already high, needs a full major rewrite" -- previously missing here
        # entirely, so the planner's policy always saw 0/None regardless of the real scan.
        "position_delete_files": stats.position_delete_files,
        "equality_delete_files": stats.equality_delete_files,
        "delete_file_pct": stats.delete_file_pct,
        "query_count_30d": stats.query_count_30d,
        "partition_col": stats.partition_col,
        "partition_col_type": stats.partition_col_type,
        "projected_savings_usd_month": stats.projected_savings_usd_month,
        "format_version": (stats.raw or {}).get("format_version"),
        "raw": dict(stats.raw or {}),
    }


def _minor_delete_runs_since_major(session: Session, table_id: uuid.UUID) -> int:
    last_major_id = session.scalar(
        select(func.max(AuditLog.id)).where(
            AuditLog.table_id == table_id,
            AuditLog.action == OperationType.CONVERT_DELETES.value,
            AuditLog.status == "OK",
        )
    )
    conditions = [
        AuditLog.table_id == table_id,
        AuditLog.action == OperationType.REWRITE_POSITION_DELETES.value,
        AuditLog.status == "OK",
    ]
    if last_major_id is not None:
        conditions.append(AuditLog.id > last_major_id)
    return int(session.scalar(select(func.count(AuditLog.id)).where(*conditions)) or 0)


def _latest_partitions(session: Session, table_id: uuid.UUID) -> Sequence[Mapping[str, Any]]:
    latest_scan = session.scalar(
        select(TablePartition.scan_at)
        .where(TablePartition.table_id == table_id)
        .order_by(desc(TablePartition.scan_at))
        .limit(1)
    )
    if latest_scan is None:
        return ()
    rows = session.scalars(
        select(TablePartition).where(
            TablePartition.table_id == table_id,
            TablePartition.scan_at == latest_scan,
        )
    )
    return [
        {
            "partition_value": row.partition_value,
            "partition_values": dict(row.partition_values or {}),
            "small_files_in_partition_count": row.small_files_in_partition_count,
            "partition_size_gb": row.partition_size_gb,
            "files_per_partition": row.files_per_partition,
            # Delete files are tracked separately from files/small (data-file-only as of
            # 2026-07-28) so convert_deletes can be scoped by where deletes are actually
            # concentrated -- a table can have a partition that's already well-compacted on
            # data files but still carries a pile of stale position-delete files, and that
            # partition won't show up as "skewed" by small_files_in_partition_count alone.
            "delete_files_in_partition_count": (row.raw or {}).get("delete_files") or 0,
            "last_write_at": (row.raw or {}).get("last_write_at"),
            # AutoComp (SIGMOD 2025): without these, the planner's benefit/MOOP-aware ranking
            # in _compare_candidates has nothing to rank on and silently falls back to
            # small_files DESC every time, regardless of how compute_benefit_score /
            # compute_moop_scores scored these partitions.
            "compaction_benefit_score": row.compaction_benefit_score,
            "autocomp_traits": dict(row.autocomp_traits) if row.autocomp_traits else None,
        }
        for row in rows
    ]


def _table_autocomp_summary(
    session: Session, table_id: uuid.UUID
) -> dict[str, float | None] | None:
    """Best (max) benefit/MOOP score across the table's latest-scan partitions.

    Job-level admission gates (governor.decide_job_admission) operate per table/job, not per
    partition, so "is this table worth admitting" is proxied by its single best candidate --
    consistent with get_top_compaction_candidates already ranking by the same score.
    """
    latest_scan = session.scalar(
        select(func.max(TablePartition.scan_at)).where(TablePartition.table_id == table_id)
    )
    if latest_scan is None:
        return None
    moop_expr = TablePartition.autocomp_traits["s_c"].astext.cast(Float)
    row = session.execute(
        select(
            func.max(TablePartition.compaction_benefit_score),
            func.max(moop_expr),
        ).where(
            TablePartition.table_id == table_id,
            TablePartition.scan_at == latest_scan,
        )
    ).one()
    max_benefit, max_moop = row
    if max_benefit is None:
        return None
    return {
        "benefit_score": float(max_benefit),
        "moop_score": float(max_moop) if max_moop is not None else None,
    }
