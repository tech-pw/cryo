"""One batch maintenance run: select, plan, run on Spark lanes, record, rescan, report.

Runs as a single CronJob pod, the same shape as the scanner. Nothing is queued: the tables,
their SQL and the pods that run it are all decided up front, the pods are deleted when the
run ends, and the run's rows (runs / jobs / audit_log) are written by this process alone.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.config import Settings
from cryo.db.models import AuditLog, Destination, MaintenanceJob, MaintenanceRun, TableStats
from cryo.domain.enums import EngineType, JobStatus, OperationType
from cryo.engines.spark.k8s import SparkK8sConfig
from cryo.maintenance.k8s_lanes import LaneRunner, LaneSpec, LaneState, PollUpdate, lane_names
from cryo.maintenance.lanes import LaneLimits, LaneTable, plan_lanes
from cryo.maintenance.manifest import (
    DEFAULT_PARAMS,
    TableData,
    TableWork,
    build_table_work,
    lane_manifest_bytes,
)
from cryo.maintenance.results import StatementResult
from cryo.maintenance.selection import RankedCandidate, load_candidates, rank_candidates
from cryo.queue.locks import LockStore

logger = logging.getLogger(__name__)

ACTOR = "cryo-batch"
CRON_TRIGGER = "batch-cron"
_ENGINE = EngineType.SPARK_EMR.value
# Lanes stop starting new tables this long before the hard stop, so a table that starts
# near the end has room to finish instead of being killed mid-commit.
_START_CUTOFF = timedelta(minutes=20)
_OWNER_TTL_SECONDS = 300


class LaneRunnerLike(Protocol):
    lanes: list[LaneState]

    def launch(
        self, specs: Sequence[LaneSpec], *, deadline_epoch: float, active_deadline_seconds: int
    ) -> None: ...

    @property
    def all_finished(self) -> bool: ...

    def poll(self) -> PollUpdate: ...

    def teardown(self) -> None: ...


@dataclass
class TableOutcome:
    table_id: uuid.UUID
    name: str
    job_id: uuid.UUID
    lane: int
    statement_count: int
    before_size_gb: float
    before_files: int
    results: dict[int, StatementResult] = field(default_factory=dict)
    status: JobStatus | None = None
    error: str | None = None

    @property
    def complete(self) -> bool:
        return len(self.results) >= self.statement_count

    @property
    def any_ok(self) -> bool:
        return any(r.ok for r in self.results.values())


@dataclass
class RunReport:
    run_id: uuid.UUID | None
    trigger: str
    dry_run: bool
    started_at: datetime
    finished_at: datetime | None = None
    selected: int = 0
    nothing_to_do: int = 0
    planning_failed: int = 0
    locked: int = 0
    lanes: list[dict[str, Any]] = field(default_factory=list)
    succeeded: int = 0
    partial: int = 0
    failed: int = 0
    skipped: int = 0
    orphan_gate_closed: bool = False
    files_rewritten: int = 0
    files_added: int = 0
    bytes_rewritten: int = 0
    orphan_files_removed: int = 0
    expired_files_deleted: int = 0
    size_before_gb: float = 0.0
    size_after_gb: float | None = None
    files_before: int = 0
    files_after: int | None = None
    rescanned: int = 0
    rescan_error: str | None = None
    failures: list[dict[str, str]] = field(default_factory=list)
    planned_statements: int = 0

    @property
    def maintained(self) -> int:
        return self.succeeded + self.partial

    def to_summary(self) -> dict[str, Any]:
        return {
            "pipeline": "batch",
            "trigger": self.trigger,
            "selected": self.selected,
            "nothing_to_do": self.nothing_to_do,
            "planning_failed": self.planning_failed,
            "locked": self.locked,
            "lanes": self.lanes,
            "succeeded": self.succeeded,
            "partial": self.partial,
            "failed": self.failed,
            "skipped": self.skipped,
            "orphan_gate_closed": self.orphan_gate_closed,
            "files_rewritten": self.files_rewritten,
            "files_added": self.files_added,
            "bytes_rewritten": self.bytes_rewritten,
            "orphan_files_removed": self.orphan_files_removed,
            "expired_files_deleted": self.expired_files_deleted,
            "size_before_gb": round(self.size_before_gb, 3),
            "size_after_gb": None if self.size_after_gb is None else round(self.size_after_gb, 3),
            "files_before": self.files_before,
            "files_after": self.files_after,
            "rescanned": self.rescanned,
            "rescan_error": self.rescan_error,
            "failures": self.failures[:20],
        }


@dataclass(frozen=True)
class _Destination:
    id: uuid.UUID
    engine: str
    config: Mapping[str, Any]


def _metric_int(metrics: Mapping[str, Any], key: str) -> int:
    value = metrics.get(key)
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def table_status(outcome: TableOutcome) -> tuple[JobStatus, str | None]:
    """Job status from whatever statement results arrived for the table."""
    results = [outcome.results[k] for k in sorted(outcome.results)]
    first_error = next((r.error for r in results if not r.ok and not r.skipped and r.error), None)
    ran = [r for r in results if not r.skipped]
    if not ran:
        skipped_reason = next((r.error for r in results if r.error), None)
        return JobStatus.SKIPPED, skipped_reason or "not started"
    ok = sum(1 for r in ran if r.ok)
    if ok == outcome.statement_count:
        return JobStatus.SUCCEEDED, None
    if ok > 0:
        return JobStatus.PARTIAL, first_error
    return JobStatus.FAILED, first_error


class BatchMaintenance:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        settings: Settings,
        lock_store: LockStore,
        lane_runner_factory: Callable[[uuid.UUID, SparkK8sConfig, str], LaneRunnerLike]
        | None = None,
        load_table: Callable[[uuid.UUID], TableData] | None = None,
        protected_snapshot_ids: Callable[[uuid.UUID], Sequence[int]] | None = None,
        refresh_shared_snapshot: Callable[[Session], None] | None = None,
        shared_snapshot: Callable[[Session], tuple[datetime | None, frozenset[uuid.UUID]]]
        | None = None,
        rescan: Callable[[Sequence[uuid.UUID]], int] | None = None,
        notify: Callable[[RunReport], bool] | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._locks = lock_store
        self._lane_runner_factory = lane_runner_factory or (
            lambda run_id, cfg, catalog: LaneRunner(run_id, cfg, catalog=catalog)
        )
        self._load_table = load_table or self._default_load_table
        self._protected = protected_snapshot_ids or self._default_protected
        self._refresh_shared = refresh_shared_snapshot or _default_refresh_shared
        self._shared_snapshot = shared_snapshot or _default_shared_snapshot
        self._rescan = rescan
        self._notify = notify
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep
        self._holder = f"{ACTOR}-{uuid.uuid4().hex[:8]}"

    # -- defaults wired to the real stores -------------------------------------------------

    def _default_load_table(self, table_id: uuid.UUID) -> TableData:
        from cryo.db.repositories.job_store import SQLJobStore

        data = SQLJobStore(self._session_factory).get_table_data(str(table_id))
        return TableData(stats=data.stats, partitions=data.partitions)

    def _default_protected(self, table_id: uuid.UUID) -> Sequence[int]:
        from cryo.db.repositories.protected_snapshots import ProtectedSnapshotRepository

        with self._session_factory() as session:
            return sorted(ProtectedSnapshotRepository(session).list_snapshot_ids(table_id))

    # -- run --------------------------------------------------------------------------------

    def run(
        self,
        *,
        trigger: str,
        table_ids: Sequence[uuid.UUID] | None = None,
        limit: int | None = None,
        dry_run: bool = False,
    ) -> RunReport:
        settings = self._settings
        started = self._clock()
        report = RunReport(run_id=None, trigger=trigger, dry_run=dry_run, started_at=started)
        deadline = started + timedelta(minutes=settings.maintenance_max_runtime_minutes)

        destination = self._resolve_destination()
        catalog = str(destination.config.get("catalog") or "")
        if not catalog:
            raise RuntimeError(f"Spark destination {destination.id} has no `catalog` configured")

        params = dict(DEFAULT_PARAMS)
        shared_ids = self._shared_location_ids(dry_run=dry_run, now=started, report=report)
        if report.orphan_gate_closed:
            params["skip_operations"] = [OperationType.REMOVE_ORPHAN_FILES.value]

        ranked = self._select(table_ids=table_ids, limit=limit, now=started)
        report.selected = len(ranked)

        work: list[tuple[RankedCandidate, TableWork]] = []
        for item in ranked:
            table_id = item.candidate.table_id
            try:
                planned = build_table_work(
                    table_id,
                    load_table=self._load_table,
                    destination=destination,
                    params=params,
                    protected_snapshot_ids=self._protected,
                    has_shared_location=lambda tid: tid in shared_ids,
                )
            except Exception as exc:
                logger.warning("Planning failed for %s", item.candidate.display_name, exc_info=True)
                report.planning_failed += 1
                report.failures.append(
                    {"table": item.candidate.display_name, "error": f"planning: {exc}"[:300]}
                )
                continue
            if planned is None:
                report.nothing_to_do += 1
                continue
            if not planned.table_ref.startswith(f"{catalog}."):
                report.planning_failed += 1
                report.failures.append(
                    {
                        "table": item.candidate.display_name,
                        "error": f"catalog of {planned.table_ref} is not {catalog}",
                    }
                )
                continue
            work.append((item, planned))
        report.planned_statements = sum(len(w.statements) for _, w in work)

        if dry_run:
            lanes = plan_lanes(
                [LaneTable(i.candidate.table_id, i.candidate.rewrite_gb) for i, _ in work],
                self._lane_limits(),
            )
            report.lanes = [
                _lane_summary(lane.index, lane.cpu_milli, lane.mem_mib, len(lane.tables))
                for lane in lanes
            ]
            report.finished_at = self._clock()
            return report
        if not work:
            report.finished_at = self._clock()
            self._record_empty_run(report, trigger)
            self._send(report)
            return report

        return self._execute(
            report=report,
            trigger=trigger,
            destination=destination,
            catalog=catalog,
            work=work,
            deadline=deadline,
        )

    def _execute(
        self,
        *,
        report: RunReport,
        trigger: str,
        destination: _Destination,
        catalog: str,
        work: list[tuple[RankedCandidate, TableWork]],
        deadline: datetime,
    ) -> RunReport:
        settings = self._settings
        run_id = uuid.uuid4()
        report.run_id = run_id
        outcomes: dict[uuid.UUID, TableOutcome] = {}
        with self._session_factory() as session:
            run = MaintenanceRun(
                id=run_id,
                kind=_run_kind(trigger),
                trigger=trigger,
                status="running",
                deadline_at=deadline,
                scheduled_at=report.started_at,
                summary={"pipeline": "batch", "trigger": trigger},
            )
            session.add(run)
            now = self._clock()
            for item, planned in work:
                job = MaintenanceJob(
                    id=uuid.uuid4(),
                    run_id=run_id,
                    table_id=planned.table_id,
                    destination_id=destination.id,
                    mode="deep",
                    plan=planned.plan,
                    status=JobStatus.RUNNING.value,
                    started_at=now,
                )
                session.add(job)
                outcomes[planned.table_id] = TableOutcome(
                    table_id=planned.table_id,
                    name=item.candidate.display_name,
                    job_id=job.id,
                    lane=-1,
                    statement_count=len(planned.statements),
                    before_size_gb=item.candidate.total_size_gb,
                    before_files=item.candidate.total_files,
                )
            session.commit()

        lock_ttl = int((deadline - self._clock()).total_seconds()) + 1800
        runnable: list[tuple[RankedCandidate, TableWork]] = []
        for item, planned in work:
            outcome = outcomes[planned.table_id]
            if not self._locks.acquire_table_lock(
                str(planned.table_id), str(outcome.job_id), lock_ttl
            ):
                outcome.status, outcome.error = JobStatus.SKIPPED, "table locked by another job"
                report.locked += 1
                continue
            self._locks.acquire_job_owner(str(outcome.job_id), self._holder, _OWNER_TTL_SECONDS)
            runnable.append((item, planned))
        self._persist_finished([o for o in outcomes.values() if o.status is not None])

        runner: LaneRunnerLike | None = None
        interrupted: BaseException | None = None
        try:
            if runnable:
                runner = self._launch(
                    run_id, destination, catalog, runnable, outcomes, deadline, report
                )
                self._watch(runner, outcomes, deadline)
        except BaseException as exc:
            # SIGTERM (CronJob deadline, eviction) arrives as SystemExit via run.py's handler.
            interrupted = exc
            raise
        finally:
            if runner is not None:
                try:
                    runner.teardown()
                except Exception:
                    logger.exception("Lane teardown failed for run %s", run_id)
            reason = (
                f"run interrupted ({type(interrupted).__name__})"
                if interrupted is not None
                else "run deadline reached"
            )
            self._finalize_unfinished(runner, outcomes, default_reason=reason)
            for outcome in outcomes.values():
                self._locks.release_job_owner(str(outcome.job_id), self._holder)
                self._locks.release_table_lock(str(outcome.table_id), str(outcome.job_id))
            if interrupted is not None:
                self._tally(report, outcomes)
                report.failures.insert(0, {"table": "(run)", "error": reason})
                report.finished_at = self._clock()
                self._finish_run(report)
                self._send(report)

        self._tally(report, outcomes)
        maintained = [o.table_id for o in outcomes.values() if o.any_ok]
        if settings.maintenance_rescan_enabled and maintained and self._rescan is not None:
            try:
                report.rescanned = self._rescan(maintained)
            except Exception as exc:
                logger.exception("Post-maintenance rescan failed")
                report.rescan_error = str(exc)[:300]
            self._after_stats(report, outcomes)
        report.finished_at = self._clock()
        self._finish_run(report)
        self._send(report)
        return report

    # -- pieces -----------------------------------------------------------------------------

    def _resolve_destination(self) -> _Destination:
        wanted = self._settings.maintenance_destination_id
        with self._session_factory() as session:
            if wanted:
                row = session.get(Destination, uuid.UUID(wanted))
                if row is None:
                    raise RuntimeError(f"maintenance destination {wanted} not found")
                rows = [row]
            else:
                rows = list(
                    session.scalars(
                        select(Destination).where(
                            Destination.enabled.is_(True),
                            Destination.engine == EngineType.SPARK_EMR.value,
                        )
                    )
                )
            if len(rows) != 1:
                raise RuntimeError(
                    f"expected exactly one enabled Spark destination, found {len(rows)}; "
                    "set CRYO_MAINTENANCE_DESTINATION_ID"
                )
            row = rows[0]
            return _Destination(id=row.id, engine=row.engine, config=dict(row.config or {}))

    def _lane_limits(self) -> LaneLimits:
        s = self._settings
        return LaneLimits(
            max_lanes=s.maintenance_max_lanes,
            max_cpu_milli=s.maintenance_lane_max_cpu_milli,
            max_mem_mib=s.maintenance_lane_max_mem_mib,
            target_gb_per_lane=s.maintenance_lane_target_gb,
        )

    def _shared_location_ids(
        self, *, dry_run: bool, now: datetime, report: RunReport
    ) -> frozenset[uuid.UUID]:
        """Overlapping-path tables, refreshed first so the orphan-removal gate is current.

        remove_orphan_files on a table whose location overlaps another table deletes that
        other table's live files. If the snapshot can't be refreshed and the last good one is
        stale (or missing), orphan removal is dropped for the whole run instead of trusting it.
        """
        if not dry_run:
            try:
                with self._session_factory() as session:
                    self._refresh_shared(session)
            except Exception:
                logger.warning("Shared-S3 snapshot refresh failed", exc_info=True)
        with self._session_factory() as session:
            generated_at, ids = self._shared_snapshot(session)
        max_age = timedelta(hours=self._settings.maintenance_shared_snapshot_max_age_hours)
        if generated_at is None or now - _aware(generated_at) > max_age:
            logger.warning(
                "Shared-S3 snapshot missing or older than %s; skipping remove_orphan_files", max_age
            )
            report.orphan_gate_closed = True
        return ids

    def _select(
        self, *, table_ids: Sequence[uuid.UUID] | None, limit: int | None, now: datetime
    ) -> list[RankedCandidate]:
        s = self._settings
        with self._session_factory() as session:
            candidates = load_candidates(
                session,
                now=now,
                cooldown_hours=s.maintenance_cooldown_hours,
                max_stats_age_days=s.maintenance_max_stats_age_days,
                table_ids=table_ids,
            )
        return rank_candidates(
            candidates,
            limit=limit or (len(candidates) if table_ids else s.maintenance_top_n),
            require_eligible=not table_ids,
        )

    def _spark_config(self, destination: _Destination) -> SparkK8sConfig:
        cfg = SparkK8sConfig.from_config(destination.config)
        if cfg.s3_access_key and cfg.s3_secret_key:
            return cfg
        from cryo.db.models import Source

        with self._session_factory() as session:
            sources = list(session.scalars(select(Source).where(Source.enabled.is_(True))))
        if len(sources) == 1:
            source_config = sources[0].config or {}
            access = source_config.get("s3.access-key-id")
            secret = source_config.get("s3.secret-access-key")
            if isinstance(access, str) and access and isinstance(secret, str) and secret:
                return replace(cfg, s3_access_key=access, s3_secret_key=secret)
        return cfg

    def _launch(
        self,
        run_id: uuid.UUID,
        destination: _Destination,
        catalog: str,
        runnable: list[tuple[RankedCandidate, TableWork]],
        outcomes: dict[uuid.UUID, TableOutcome],
        deadline: datetime,
        report: RunReport,
    ) -> LaneRunnerLike:
        by_id = {planned.table_id: planned for _, planned in runnable}
        lanes = plan_lanes(
            [LaneTable(item.candidate.table_id, item.candidate.rewrite_gb) for item, _ in runnable],
            self._lane_limits(),
        )
        specs: list[LaneSpec] = []
        for lane in lanes:
            lane_work = [by_id[t.table_id] for t in lane.tables]
            for t in lane.tables:
                outcomes[t.table_id].lane = lane.index
            specs.append(
                LaneSpec(
                    index=lane.index,
                    cpu_milli=lane.cpu_milli,
                    mem_mib=lane.mem_mib,
                    manifest=lane_manifest_bytes(run_id, lane.index, lane_work),
                )
            )
            report.lanes.append(
                _lane_summary(lane.index, lane.cpu_milli, lane.mem_mib, len(lane.tables))
            )
        with self._session_factory() as session:
            for lane in lanes:
                name = lane_names(run_id, lane.index)
                for t in lane.tables:
                    job = session.get(MaintenanceJob, outcomes[t.table_id].job_id)
                    if job is not None:
                        job.engine_ref = name
                        job.replica = name
            session.commit()
        runner = self._lane_runner_factory(run_id, self._spark_config(destination), catalog)
        now = self._clock()
        runner.launch(
            specs,
            deadline_epoch=(deadline - _START_CUTOFF).timestamp(),
            active_deadline_seconds=int((deadline - now).total_seconds()),
        )
        return runner

    def _watch(
        self,
        runner: LaneRunnerLike,
        outcomes: dict[uuid.UUID, TableOutcome],
        deadline: datetime,
    ) -> None:
        # A minute past the Jobs' own activeDeadlineSeconds, so Kubernetes has killed the pods
        # and the final log read sees everything they printed.
        hard_stop = deadline + timedelta(minutes=1)
        while True:
            update = runner.poll()
            self._apply(update, outcomes, runner)
            if runner.all_finished:
                return
            if self._clock() >= hard_stop:
                logger.warning("Run deadline passed with lanes still running; tearing down")
                return
            for outcome in outcomes.values():
                if outcome.status is None:
                    self._locks.renew_job_owner(
                        str(outcome.job_id), self._holder, _OWNER_TTL_SECONDS
                    )
            self._sleep(self._settings.maintenance_poll_seconds)

    def _apply(
        self,
        update: PollUpdate,
        outcomes: dict[uuid.UUID, TableOutcome],
        runner: LaneRunnerLike,
    ) -> None:
        if not update.new_results and not update.newly_finished:
            return
        lane_jobs = {lane.spec.index: lane.job_name for lane in runner.lanes}
        audit: list[AuditLog] = []
        finished: list[TableOutcome] = []
        for lane_index, result in update.new_results:
            outcome = outcomes.get(result.table_id)
            if outcome is None or outcome.status is not None:
                continue
            outcome.results[result.seq] = result
            audit.append(_audit_row(outcome, result, lane_jobs.get(lane_index)))
            if outcome.complete:
                outcome.status, outcome.error = table_status(outcome)
                finished.append(outcome)
        if audit:
            with self._session_factory() as session:
                session.add_all(audit)
                session.commit()
        self._persist_finished(finished)
        for outcome in finished:
            self._locks.release_job_owner(str(outcome.job_id), self._holder)
            self._locks.release_table_lock(str(outcome.table_id), str(outcome.job_id))

    def _finalize_unfinished(
        self,
        runner: LaneRunnerLike | None,
        outcomes: dict[uuid.UUID, TableOutcome],
        *,
        default_reason: str,
    ) -> None:
        lanes = {lane.spec.index: lane for lane in (runner.lanes if runner else [])}
        leftover: list[TableOutcome] = []
        for outcome in outcomes.values():
            if outcome.status is not None:
                continue
            lane = lanes.get(outcome.lane)
            reason = (lane.exit_reason if lane else None) or default_reason
            started = lane is not None and outcome.table_id in lane.started_tables
            if outcome.results or started:
                status, error = table_status(outcome)
                if status is JobStatus.SUCCEEDED:
                    status = JobStatus.PARTIAL
                if status is JobStatus.SKIPPED:
                    status = JobStatus.FAILED
                outcome.status = status
                outcome.error = f"lane pod ended mid-table: {reason}" + (
                    f"; {error}" if error else ""
                )
            else:
                outcome.status = JobStatus.SKIPPED
                outcome.error = f"not reached before the lane ended: {reason}"
            leftover.append(outcome)
        self._persist_finished(leftover)

    def _persist_finished(self, finished: Sequence[TableOutcome]) -> None:
        if not finished:
            return
        now = self._clock()
        with self._session_factory() as session:
            for outcome in finished:
                job = session.get(MaintenanceJob, outcome.job_id)
                if job is None or outcome.status is None:
                    continue
                job.status = outcome.status.value
                job.error = outcome.error[:2000] if outcome.error else None
                job.finished_at = now
            session.commit()

    def _tally(self, report: RunReport, outcomes: Mapping[uuid.UUID, TableOutcome]) -> None:
        for outcome in outcomes.values():
            if outcome.status is JobStatus.SUCCEEDED:
                report.succeeded += 1
            elif outcome.status is JobStatus.PARTIAL:
                report.partial += 1
            elif outcome.status is JobStatus.FAILED:
                report.failed += 1
            else:
                report.skipped += 1
            if outcome.status in {JobStatus.FAILED, JobStatus.PARTIAL} and outcome.error:
                report.failures.append({"table": outcome.name, "error": outcome.error[:300]})
            for result in outcome.results.values():
                if not result.ok:
                    continue
                m = result.metrics
                if result.action in {
                    OperationType.OPTIMIZE.value,
                    OperationType.CONVERT_DELETES.value,
                }:
                    report.files_rewritten += _metric_int(m, "rewritten_data_files_count")
                    report.files_added += _metric_int(m, "added_data_files_count")
                    report.bytes_rewritten += _metric_int(m, "rewritten_bytes_count")
                elif result.action == OperationType.REMOVE_ORPHAN_FILES.value:
                    report.orphan_files_removed += int(result.rows or 0)
                elif result.action == OperationType.EXPIRE_SNAPSHOTS.value:
                    report.expired_files_deleted += sum(
                        _metric_int(m, key)
                        for key in (
                            "deleted_data_files_count",
                            "deleted_position_delete_files_count",
                            "deleted_equality_delete_files_count",
                            "deleted_manifest_files_count",
                            "deleted_manifest_lists_count",
                        )
                    )

    def _after_stats(self, report: RunReport, outcomes: Mapping[uuid.UUID, TableOutcome]) -> None:
        maintained = {o.table_id: o for o in outcomes.values() if o.any_ok}
        if not maintained:
            return
        with self._session_factory() as session:
            rows = session.execute(
                select(TableStats.table_id, TableStats.total_size_gb, TableStats.total_files).where(
                    TableStats.table_id.in_(list(maintained))
                )
            ).all()
        after = {row[0]: (float(row[1] or 0.0), int(row[2] or 0)) for row in rows}
        report.size_before_gb = sum(o.before_size_gb for o in maintained.values())
        report.files_before = sum(o.before_files for o in maintained.values())
        report.size_after_gb = sum(
            after.get(tid, (o.before_size_gb, o.before_files))[0] for tid, o in maintained.items()
        )
        report.files_after = sum(
            after.get(tid, (o.before_size_gb, o.before_files))[1] for tid, o in maintained.items()
        )

    def _finish_run(self, report: RunReport) -> None:
        if report.run_id is None:
            return
        with self._session_factory() as session:
            run = session.get(MaintenanceRun, report.run_id)
            if run is None:
                return
            # Skipped tables (deadline, lock, interruption) are unfinished scope, not success.
            if report.failed == 0 and report.partial == 0 and report.skipped == 0:
                run.status = "succeeded"
            elif report.maintained > 0:
                run.status = "partial"
            else:
                run.status = "failed"
            run.finished_at = report.finished_at
            run.summary = report.to_summary()
            session.commit()

    def _record_empty_run(self, report: RunReport, trigger: str) -> None:
        with self._session_factory() as session:
            run = MaintenanceRun(
                id=uuid.uuid4(),
                kind=_run_kind(trigger),
                trigger=trigger,
                status="succeeded",
                scheduled_at=report.started_at,
                finished_at=report.finished_at,
                summary=report.to_summary(),
            )
            session.add(run)
            session.commit()
            report.run_id = run.id

    def _send(self, report: RunReport) -> None:
        if not self._settings.maintenance_slack_enabled or self._notify is None:
            return
        try:
            self._notify(report)
        except Exception:
            logger.exception("Slack report for run %s failed", report.run_id)


def _run_kind(trigger: str) -> str:
    return "scheduled" if trigger == CRON_TRIGGER else "adhoc"


def _lane_summary(index: int, cpu_milli: int, mem_mib: int, tables: int) -> dict[str, Any]:
    return {"lane": index, "cpu_milli": cpu_milli, "mem_mib": mem_mib, "tables": tables}


def _audit_row(outcome: TableOutcome, result: StatementResult, engine_ref: str | None) -> AuditLog:
    if result.ok:
        status = "OK"
        detail = f"seq {result.seq}; rows={result.rows}; metrics={result.metrics}"
    elif result.skipped:
        status = "SKIPPED"
        detail = result.error or "skipped"
    else:
        status = "FAILED"
        detail = result.error or "failed"
    optimize_like = result.action in {
        OperationType.OPTIMIZE.value,
        OperationType.CONVERT_DELETES.value,
    }
    return AuditLog(
        table_id=outcome.table_id,
        job_id=outcome.job_id,
        action=result.action,
        status=status,
        engine=_ENGINE,
        detail=detail[:4000],
        duration_ms=result.duration_ms,
        input_files_count=_metric_int(result.metrics, "rewritten_data_files_count")
        if optimize_like and result.ok
        else None,
        output_files_count=_metric_int(result.metrics, "added_data_files_count")
        if optimize_like and result.ok
        else None,
        actor=ACTOR,
        engine_ref=engine_ref,
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _default_refresh_shared(session: Session) -> None:
    from cryo.anomalies.report import refresh_shared_s3_report

    refresh_shared_s3_report(session)


def _default_shared_snapshot(session: Session) -> tuple[datetime | None, frozenset[uuid.UUID]]:
    from cryo.anomalies.report import latest_shared_s3_report, shared_location_table_ids

    latest = latest_shared_s3_report(session)
    if latest is None:
        return None, frozenset()
    return latest.generated_at, shared_location_table_ids(session)
