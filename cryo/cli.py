#!/usr/bin/env python3
"""Cryo CLI - Entry points for maintenance daemons.

Usage:
    cryo governor [--wave-interval SECS]
    cryo worker [--poll-interval SECS]
    cryo scheduler [--check-interval SECS]
    cryo scanner [--poll-interval SECS]
    cryo serve  # runs governor, worker, scheduler, and scanner queue consumer
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from datetime import UTC, datetime
from threading import Event, Thread
from typing import Any, NoReturn

from cryo.config import get_settings
from cryo.observability.logging import configure_logging
from cryo.observability.tracing import init_tracing, shutdown_tracing

settings = get_settings()
configure_logging(
    level="DEBUG" if settings.environment == "dev" else "INFO",
    json_format=settings.environment in {"staging", "prod"},
    environment=settings.environment,
)
logger = logging.getLogger("cryo")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cryo",
        description="Cryo Iceberg Maintenance Platform CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Governor command
    gov_parser = subparsers.add_parser("governor", help="Run the admission governor")
    gov_parser.add_argument(
        "--wave-interval",
        type=int,
        default=30,
        help="Seconds between admission waves (default: 30)",
    )

    # Worker command
    worker_parser = subparsers.add_parser("worker", help="Run a maintenance worker")
    worker_parser.add_argument(
        "--poll-interval",
        type=int,
        default=5,
        help="Seconds between job polls (default: 5)",
    )

    # Scheduler command
    sched_parser = subparsers.add_parser("scheduler", help="Run the cron scheduler")
    sched_parser.add_argument(
        "--check-interval",
        type=int,
        default=60,
        help="Seconds between schedule checks (default: 60)",
    )

    scanner_parser = subparsers.add_parser(
        "scanner",
        help="Run the ad-hoc scan queue consumer",
    )
    scanner_parser.add_argument(
        "--poll-interval",
        type=int,
        default=5,
        help="Seconds between scan queue polls (default: 5)",
    )

    # Serve command (all-in-one)
    serve_parser = subparsers.add_parser(
        "serve",
        help="Run governor, worker, scheduler, and scanner queue consumer",
    )
    serve_parser.add_argument("--wave-interval", type=int, default=30)
    serve_parser.add_argument("--poll-interval", type=int, default=5)
    serve_parser.add_argument("--check-interval", type=int, default=60)

    events_parser = subparsers.add_parser("events", help="Run event watcher + consumer loop")
    events_parser.add_argument(
        "--poll-interval",
        type=int,
        default=5,
        help="Seconds between snapshot polls (default: 5)",
    )

    return parser


class DaemonRunner:
    """Base class for daemon loops with graceful shutdown."""

    def __init__(self, name: str, interval: int) -> None:
        self.name = name
        self.interval = interval
        self._stop = Event()
        self._logger = logging.getLogger(f"cryo.{name}")

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        self._logger.info("Starting %s (interval=%ds)", self.name, self.interval)
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                self._logger.exception("Error in %s tick", self.name)
            self._stop.wait(self.interval)
        self._logger.info("Stopped %s", self.name)

    def _tick(self) -> None:
        raise NotImplementedError


class BenefitScoreUpdaterRunner(DaemonRunner):
    """Runs periodic AutoComp benefit score refreshes."""

    def __init__(self, interval: int | None = None) -> None:
        settings_interval = get_settings().benefit_score_update_interval_hours * 3600
        super().__init__(
            "benefit-score-updater",
            interval or settings_interval,
        )

    def _tick(self) -> None:
        from cryo.scheduler.benefit_score_updater import run_benefit_score_update

        partitions_updated = run_benefit_score_update()
        if partitions_updated:
            self._logger.info(
                "Updated benefit scores for %s partitions",
                partitions_updated,
            )


class AlertDispatcherRunner(DaemonRunner):
    """Checks owned/subscribed tables for SLA/data-quality breaches and notifies them."""

    def __init__(self, interval: int | None = None) -> None:
        settings_interval = get_settings().table_alert_dispatch_interval_hours * 3600
        super().__init__(
            "table-alert-dispatcher",
            interval or settings_interval,
        )

    def _tick(self) -> None:
        from cryo.scheduler.alert_dispatcher import run_alert_dispatch

        sent = run_alert_dispatch()
        if sent:
            self._logger.info("Dispatched %s table breach alert(s)", sent)


class S3LogIngestRunner(DaemonRunner):
    """Runs S3 request-cost ingestion for every enabled bucket, then prunes old daily rows.

    Opt-in per bucket (S3BucketLogConfig.enabled) -- a bucket with no config row, or one that's
    disabled, is simply skipped. See docs/s3-request-cost-tracking.md §10 for the capacity math
    this interval is meant to hold to (one query per enabled bucket per run, low tens of
    seconds each per §5's back-of-envelope estimate).
    """

    def __init__(self, interval: int | None = None) -> None:
        settings_interval = get_settings().s3_log_ingest_interval_hours * 3600
        super().__init__("s3-log-ingest", interval or settings_interval)

    def _tick(self) -> None:
        from cryo.db.session import SessionLocal
        from cryo.scanner.s3_request_cost_ingest import (
            prune_partition_access_daily,
            prune_s3_request_cost_daily,
            run_s3_ingest_tick,
        )

        settings = get_settings()
        with SessionLocal() as session:
            ingested = run_s3_ingest_tick(session)
            pruned = prune_s3_request_cost_daily(
                session, retention_days=settings.s3_request_cost_retention_days
            )
            # Same retention window as its sibling above -- see PartitionAccessDaily's
            # docstring for why this doesn't warrant its own separate setting.
            partition_pruned = prune_partition_access_daily(
                session, retention_days=settings.s3_request_cost_retention_days
            )
            session.commit()
            if ingested or pruned or partition_pruned:
                self._logger.info(
                    "S3 request-cost tick: ingested %s bucket(s), pruned %s stale row(s), "
                    "%s stale partition-access row(s)",
                    ingested,
                    pruned,
                    partition_pruned,
                )


class GovernorRunner(DaemonRunner):
    """Runs the admission governor wave loop."""

    # How often to re-run the orphaned-lock sweep after the mandatory startup pass.
    # A one-shot startup sweep only catches locks orphaned by a restart; it misses
    # locks orphaned mid-life by a dispatch-path exception (native_admit.py's
    # create_dispatch_record/pack_jobs/etc calls aren't all individually guarded --
    # hardened in admit_native_k8s_jobs, but a periodic safety net costs one cheap
    # Redis SCAN + a handful of DB lookups and catches whatever that hardening
    # doesn't). At larger table counts the absolute rate of these events rises even
    # if the per-operation rate doesn't, so don't rely on "only ever restarts once."
    _LOCK_RECONCILE_INTERVAL_SECONDS = 600

    # Spark-on-K8s cluster-mode driver pods are never owned by Cryo's wrapper
    # dispatch Job (see cryo.engines.spark.k8s.cleanup_stale_driver_pods's
    # docstring) so nothing else in the cluster ever deletes them -- confirmed
    # live, 2026-08-06: 3,276 accumulated with zero owner references. Listing is
    # a single cheap call; gate the sweep so it isn't repeated every 30s tick.
    _SPARK_POD_CLEANUP_INTERVAL_SECONDS = 600

    def __init__(self, interval: int = 30, wiring: Any = None) -> None:
        super().__init__("governor", interval)
        self._wiring: Any = wiring
        self._governor: Any = None
        self._last_lock_reconcile_at: float = 0.0
        self._last_spark_pod_cleanup_at: float = 0.0

    def _tick(self) -> None:
        governor = self._get_governor()
        if governor is None:
            self._logger.warning("Governor not available (missing dependencies)")
            return
        wiring = self._get_wiring()
        if wiring and not wiring.is_governor_leader():
            return

        now_monotonic = time.monotonic()
        if now_monotonic - self._last_lock_reconcile_at >= self._LOCK_RECONCILE_INTERVAL_SECONDS:
            self._reconcile_orphaned_table_locks(wiring)
            self._last_lock_reconcile_at = now_monotonic
        if (
            now_monotonic - self._last_spark_pod_cleanup_at
            >= self._SPARK_POD_CLEANUP_INTERVAL_SECONDS
        ):
            self._cleanup_stale_spark_driver_pods()
            self._last_spark_pod_cleanup_at = now_monotonic

        # Reconcile direct Kubernetes dispatches before admitting new work so a
        # restarted governor cannot duplicate a still-running Job whose DB
        # member status had not yet been advanced.
        self._tick_native_watcher(wiring)
        self._tick_scan_dispatch(wiring)
        result = governor.run_wave(now=datetime.now(UTC))
        if any(
            (result.admitted, result.deferred, result.blocked, result.orphaned, result.re_enqueued)
        ):
            self._logger.info(
                "Wave complete: admitted=%d, deferred=%d, blocked=%d, orphaned=%d, re_enqueued=%d",
                result.admitted,
                result.deferred,
                result.blocked,
                result.orphaned,
                result.re_enqueued,
            )

    def _tick_native_watcher(self, wiring: Any) -> None:
        try:
            from cryo.db.session import SessionLocal
            from cryo.engines.native.watcher import NativeJobWatcher

            watcher = NativeJobWatcher(
                session_factory=SessionLocal,
                lock_store=wiring.lock_store,
                mark_status=wiring.job_store.mark_status,
                record_audit=wiring.job_store.record_audit,
                run_events=getattr(wiring, "run_events", None),
            )
            watch = watcher.tick()
            if watch.finalized:
                self._logger.info(
                    "Native JobWatcher: finalized=%d still_running=%d errors=%d",
                    watch.finalized,
                    watch.still_running,
                    watch.errors,
                )
        except Exception as exc:
            self._logger.debug("Native JobWatcher tick skipped: %s", exc)

    def _cleanup_stale_spark_driver_pods(self) -> None:
        import os

        from cryo.engines.native.job_state import current_k8s_namespace
        from cryo.engines.spark.k8s import cleanup_stale_driver_pods

        try:
            namespace = current_k8s_namespace()
            older_than_seconds = int(
                os.environ.get("CRYO_SPARK_DRIVER_POD_CLEANUP_TTL_SECONDS", "3600")
            )
            deleted = cleanup_stale_driver_pods(
                namespace=namespace, older_than_seconds=older_than_seconds
            )
            if deleted:
                self._logger.info("Spark driver pod cleanup: deleted=%d", deleted)
        except Exception as exc:
            self._logger.debug("Spark driver pod cleanup tick skipped: %s", exc)

    def _tick_scan_dispatch(self, wiring: Any) -> None:
        import os

        from cryo.governor.scan_admit import admit_scan_jobs, scan_job_dispatch_enabled

        if not scan_job_dispatch_enabled():
            return
        try:
            from cryo.db.session import SessionLocal
            from cryo.queue.scan_queue import RedisScanQueue

            queue = RedisScanQueue(wiring.redis_client)
            batch_size = int(os.environ.get("CRYO_SCANNER_JOB_DISPATCH_BATCH_SIZE", "1"))
            with SessionLocal() as session:
                result = admit_scan_jobs(
                    queue=queue,
                    session=session,
                    session_factory=SessionLocal,
                    max_dispatch=batch_size,
                )
            if result.admitted:
                self._logger.info("Scan dispatch: admitted=%d", result.admitted)
        except Exception as exc:
            self._logger.debug("Scan Job dispatch tick skipped: %s", exc)

    def _reconcile_orphaned_table_locks(self, wiring: Any) -> None:
        """Release table locks whose owning job isn't actually running.

        Governor's admission loop acquires a table lock in one step (governor.py's
        per-job loop) and only dispatches or safely defers-with-release in a later
        step (native_admit.py, processing the buffered jobs). If the process dies in
        that window -- e.g. a rollout restart mid-admission -- the lock is orphaned:
        no code path ever runs to release it, and it would otherwise sit until its
        TTL expires (up to lock_ttl_seconds, default 6h), blocking every future
        admission attempt for that table with "table_locked". Confirmed live,
        2026-07-22: two tables stuck exactly this way after repeated Governor
        restarts during same-day deploys. Run once per process lifetime (guarded by
        the caller), on the leader only, since it's a global sweep, not a per-tick
        concern.
        """
        try:
            from cryo.domain.enums import JobStatus

            lock_store = wiring.lock_store
            job_store = wiring.job_store
            released = 0
            for table_id, job_id in lock_store.list_table_locks():
                try:
                    record = job_store.get_job(job_id)
                except KeyError:
                    lock_store.release_table_lock(table_id, job_id)
                    released += 1
                    continue
                if record.status is not JobStatus.RUNNING:
                    lock_store.release_table_lock(table_id, job_id)
                    released += 1
            if released:
                self._logger.info(
                    "Startup lock reconciliation: released %d orphaned table lock(s)",
                    released,
                )
        except Exception as exc:
            self._logger.warning("Table lock reconciliation failed: %s", exc)

    def _get_wiring(self) -> Any:
        if self._wiring is not None:
            return self._wiring
        from cryo.infra.wiring import build_runtime_wiring

        self._wiring = build_runtime_wiring()
        return self._wiring

    def _get_governor(self) -> Any:
        if self._governor is not None:
            return self._governor

        try:
            from cryo.governor import Governor, LiveCapacityProvider

            wiring = self._get_wiring()
            self._governor = Governor(
                queue=wiring.queue,
                lock_store=wiring.lock_store,
                store=wiring.job_store,
                # LiveCapacityProvider defaults to NativeJobStateReader (CRYO_K8S_NAMESPACE)
                # and falls back to Redis in-flight when not in-cluster.
                capacity_provider=LiveCapacityProvider(wiring.queue),
            )
            return self._governor
        except Exception as exc:
            self._logger.debug("Cannot initialize governor: %s", exc)
            return None


class WorkerRunner(DaemonRunner):
    """Runs the worker job processing loop."""

    def __init__(self, interval: int = 5, wiring: Any = None) -> None:
        super().__init__("worker", interval)
        self._wiring: Any = wiring
        self._worker: Any = None

    def _tick(self) -> None:
        worker = self._get_worker()
        if worker is None:
            self._logger.warning("Worker not available (missing dependencies)")
            return

        try:
            from cryo.config import get_settings
            from cryo.db.repositories.intents import IntentStore
            from cryo.db.repositories.job_steps import JobStepStore
            from cryo.db.session import SessionLocal
            from cryo.worker.intent_reconciler import IntentReconciler
            from cryo.worker.orphan_recovery import default_worker_holder_id, recover_orphaned_jobs

            wiring = self._get_wiring()
            step_ledger = JobStepStore(SessionLocal)
            if get_settings().durable_intents_enabled:
                IntentReconciler(
                    intents=IntentStore(SessionLocal),
                    jobs=wiring.job_store,
                    queue=wiring.queue,
                    step_ledger=step_ledger,
                ).run_once()
            recover_orphaned_jobs(
                store=wiring.job_store,
                queue=wiring.queue,
                lock_store=wiring.lock_store,
                step_ledger=step_ledger,
                holder_id=default_worker_holder_id(),
            )
        except Exception as exc:
            self._logger.warning("Worker reconciliation tick failed: %s", exc)

        job = worker.process_next()
        if job is not None:
            self._logger.info(
                "Processed job %s for table %s: %s",
                job.id[:8],
                job.table_id,
                job.status.value,
            )

    def _get_wiring(self) -> Any:
        if self._wiring is not None:
            return self._wiring
        from cryo.infra.wiring import build_runtime_wiring

        self._wiring = build_runtime_wiring()
        return self._wiring

    def _get_worker(self) -> Any:
        if self._worker is not None:
            return self._worker

        try:
            from cryo.db.repositories.intents import IntentStore
            from cryo.db.repositories.job_steps import JobStepStore
            from cryo.db.repositories.run_finalizer import finalize_open_runs
            from cryo.db.session import SessionLocal
            from cryo.worker.worker import Worker

            wiring = self._get_wiring()

            def finalize_finished_runs() -> None:
                finalize_open_runs(SessionLocal)

            self._worker = Worker(
                queue=wiring.queue,
                store=wiring.job_store,
                lock_store=wiring.lock_store,
                on_job_finished=finalize_finished_runs,
                run_events=wiring.run_events,
                step_ledger=JobStepStore(SessionLocal),
                intent_store=IntentStore(SessionLocal),
            )
            return self._worker
        except Exception as exc:
            self._logger.debug("Cannot initialize worker: %s", exc)
            return None


class SchedulerRunner(DaemonRunner):
    """Runs the scheduler cron evaluation loop."""

    def __init__(self, interval: int = 60, wiring: Any = None) -> None:
        super().__init__("scheduler", interval)
        self._wiring: Any = wiring
        self._service: Any = None
        self._scan_service: Any = None

    def _tick(self) -> None:
        wiring = self._get_wiring()
        if wiring and not wiring.is_scheduler_leader():
            return
        service = self._get_service()
        if service is None:
            self._logger.warning("Scheduler not available")
            return
        result = service.tick()
        if result.runs_created:
            self._logger.info(
                "Scheduler tick: runs=%d jobs=%d presets=%s",
                result.runs_created,
                result.jobs_enqueued,
                ",".join(result.preset_ids),
            )
        scan_service = self._get_scan_service()
        if scan_service is not None:
            scan_result = scan_service.tick()
            if scan_result.runs_created:
                self._logger.info(
                    "Scanner schedule tick: run=%s chunks=%d",
                    scan_result.run_id,
                    scan_result.chunks_enqueued,
                )

    def _get_wiring(self) -> Any:
        if self._wiring is not None:
            return self._wiring
        from cryo.infra.wiring import build_runtime_wiring

        self._wiring = build_runtime_wiring()
        return self._wiring

    def _get_service(self) -> Any:
        if self._service is not None:
            return self._service

        try:
            from cryo.scheduler.service import SchedulerService

            wiring = self._get_wiring()
            self._service = SchedulerService(wiring.scheduler_store, wiring.queue)
            return self._service
        except Exception as exc:
            self._logger.debug("Cannot initialize scheduler: %s", exc)
            return None

    def _get_scan_service(self) -> Any:
        if self._scan_service is not None:
            return self._scan_service
        try:
            from cryo.queue.scan_queue import RedisScanQueue
            from cryo.scanner.scheduler import ScanSchedulerService

            wiring = self._get_wiring()
            self._scan_service = ScanSchedulerService(
                RedisScanQueue(wiring.redis_client),
                wiring.redis_client,
            )
            return self._scan_service
        except Exception as exc:
            self._logger.debug("Cannot initialize scanner scheduler: %s", exc)
            return None


class EventsRunner(DaemonRunner):
    """Runs hot-table watcher and Redis Streams consumer."""

    def __init__(self, interval: int = 5, wiring: Any = None) -> None:
        super().__init__("events", interval)
        self._wiring: Any = wiring
        self._watcher: Any = None

    def _tick(self) -> None:
        from cryo.events.service import poll_events_once

        watcher = self._get_watcher()
        if watcher is not None:
            watcher.poll_once()
        poll_events_once(self._get_wiring())

    def _get_wiring(self) -> Any:
        if self._wiring is not None:
            return self._wiring
        from cryo.infra.wiring import build_runtime_wiring

        self._wiring = build_runtime_wiring()
        return self._wiring

    def _get_watcher(self) -> Any:
        if self._watcher is not None:
            return self._watcher
        try:
            from cryo.events.service import build_event_watcher

            self._watcher = build_event_watcher(self._get_wiring())
            return self._watcher
        except Exception as exc:
            self._logger.debug("Cannot initialize event watcher: %s", exc)
            return None


class ScannerRunner(DaemonRunner):
    """Runs queued ad-hoc scanner requests."""

    _RECONCILE_INTERVAL_SECONDS = 60
    _STALE_CHUNK_SECONDS = 900

    def __init__(self, interval: int = 5, wiring: Any = None) -> None:
        super().__init__("scanner", interval)
        self._wiring: Any = wiring
        self._worker: Any = None
        self._last_reconcile_at: float = 0.0

    def _tick(self) -> None:
        self._maybe_reconcile_stale_chunks()
        worker = self._get_worker()
        if worker is None:
            self._logger.warning("Scanner worker not available")
            return
        result = worker.process_next()
        if result is not None and result.run_id:
            self._logger.info("Processed scan run %s: %s", result.run_id[:8], result.status)

    def _maybe_reconcile_stale_chunks(self) -> None:
        """Runs at most once every _RECONCILE_INTERVAL_SECONDS, not on every 5s poll tick --
        this is a periodic sweep, not per-tick work, so it shouldn't add DB load to every
        replica's tight poll loop."""
        now = time.monotonic()
        if now - self._last_reconcile_at < self._RECONCILE_INTERVAL_SECONDS:
            return
        self._last_reconcile_at = now
        try:
            from cryo.db.session import SessionLocal
            from cryo.queue.scan_queue import RedisScanQueue
            from cryo.scanner.control import reconcile_stale_scan_chunks

            wiring = self._get_wiring()
            reconciled = reconcile_stale_scan_chunks(
                SessionLocal,
                RedisScanQueue(wiring.redis_client),
                stale_after_seconds=self._STALE_CHUNK_SECONDS,
            )
            if reconciled:
                self._logger.warning("Re-queued %d stale scan chunk(s)", reconciled)
        except Exception:
            self._logger.exception("Failed to reconcile stale scan chunks")

    def _get_wiring(self) -> Any:
        if self._wiring is not None:
            return self._wiring
        from cryo.infra.wiring import build_runtime_wiring

        self._wiring = build_runtime_wiring()
        return self._wiring

    def _get_worker(self) -> Any:
        if self._worker is not None:
            return self._worker
        try:
            from cryo.queue.scan_queue import RedisScanQueue
            from cryo.scanner.worker import ScanWorker

            wiring = self._get_wiring()
            self._worker = ScanWorker(
                RedisScanQueue(wiring.redis_client),
                run_events=wiring.run_events,
            )
            return self._worker
        except Exception as exc:
            self._logger.debug("Cannot initialize scanner worker: %s", exc)
            return None


def run_governor(args: argparse.Namespace) -> NoReturn:
    runner = GovernorRunner(interval=args.wave_interval)
    _run_with_signal(runner, service_name="governor")


def run_worker(args: argparse.Namespace) -> NoReturn:
    runner = WorkerRunner(interval=args.poll_interval)
    _run_with_signal(runner, service_name="worker")


def run_scheduler(args: argparse.Namespace) -> NoReturn:
    from cryo.reporting.scheduler import ReportScheduler
    from cryo.scheduler.benefit_score_updater import benefit_score_updater_loop_sync

    updater_stop = Event()
    updater_thread = Thread(
        target=benefit_score_updater_loop_sync,
        kwargs={"stop_event": updater_stop},
        name="benefit-score-updater",
        daemon=True,
    )
    updater_thread.start()

    alert_dispatcher = AlertDispatcherRunner()
    alert_thread = Thread(target=alert_dispatcher.run, name="table-alert-dispatcher", daemon=True)
    alert_thread.start()

    s3_log_ingest = S3LogIngestRunner()
    s3_log_ingest_thread = Thread(target=s3_log_ingest.run, name="s3-log-ingest", daemon=True)
    s3_log_ingest_thread.start()

    # Fires SlackConfig rows' daily_report_cron (per-config, DB-driven -- distinct from the
    # AlertDispatcherRunner above, which is real-time per-table breach alerts). Previously only
    # reachable via a standalone `python -m cryo.reporting.scheduler` process that no deployment
    # path actually started.
    report_scheduler = ReportScheduler()
    report_thread = Thread(target=report_scheduler.run, name=report_scheduler.name, daemon=True)
    report_thread.start()
    try:
        runner = SchedulerRunner(interval=args.check_interval)
        _run_with_signal(runner, service_name="scheduler")
    finally:
        updater_stop.set()
        updater_thread.join(timeout=5)
        alert_dispatcher.stop()
        alert_thread.join(timeout=5)
        s3_log_ingest.stop()
        s3_log_ingest_thread.join(timeout=5)
        report_scheduler.stop()
        report_thread.join(timeout=5)


def run_events(args: argparse.Namespace) -> NoReturn:
    runner = EventsRunner(interval=args.poll_interval)
    _run_with_signal(runner)


def run_scanner(args: argparse.Namespace) -> NoReturn:
    runner = ScannerRunner(interval=args.poll_interval)
    _run_with_signal(runner, service_name="scanner")


def run_serve(args: argparse.Namespace) -> NoReturn:
    """Run all daemons in separate threads with shared Redis/Postgres wiring."""
    from cryo.infra.wiring import build_runtime_wiring

    wiring = build_runtime_wiring()
    settings = get_settings()
    from cryo.observability.health_server import start_health_server

    health_services = (
        ("governor", settings.health_port),
        ("worker", settings.health_port + 1),
        ("scheduler", settings.health_port + 2),
        ("scanner", settings.health_port + 3),
    )
    from cryo.reporting.scheduler import ReportScheduler

    # Split so the health-check wiring below keeps precise DaemonRunner typing (it reaches into
    # runner._stop) -- ReportScheduler duck-types .run()/.stop()/.name but isn't a DaemonRunner
    # (its lifecycle is an async loop, not the sync _tick()/wait() shape the others share).
    core_runners: list[DaemonRunner] = [
        GovernorRunner(interval=args.wave_interval, wiring=wiring),
        WorkerRunner(interval=args.poll_interval, wiring=wiring),
        SchedulerRunner(interval=args.check_interval, wiring=wiring),
        ScannerRunner(interval=args.poll_interval, wiring=wiring),
    ]
    other_runners: list[DaemonRunner | ReportScheduler] = [
        BenefitScoreUpdaterRunner(),
        AlertDispatcherRunner(),
        S3LogIngestRunner(),
        ReportScheduler(),
    ]
    runners: list[DaemonRunner | ReportScheduler] = [*core_runners, *other_runners]
    for runner, (service_name, port) in zip(core_runners, health_services, strict=True):

        def _readiness(r: DaemonRunner = runner) -> bool:
            return not r._stop.is_set()

        start_health_server(
            port=port,
            service_name=service_name,
            readiness_check=_readiness,
        )

    threads: list[Thread] = []
    for bg_runner in runners:
        thread = Thread(target=bg_runner.run, name=bg_runner.name, daemon=True)
        thread.start()
        threads.append(thread)

    stop = Event()

    def handle_signal(signum: int, frame: object) -> None:
        logger.info("Received signal %d, shutting down...", signum)
        for bg_runner in runners:
            bg_runner.stop()
        stop.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    logger.info(
        "Cryo serve started (governor + worker + scheduler + scanner + benefit scores + alerts)"
    )
    while not stop.is_set():
        time.sleep(1)

    for thread in threads:
        thread.join(timeout=5)

    logger.info("Shutdown complete")
    sys.exit(0)


def _worker_scale_metric() -> int:
    """Count of jobs that need a worker to make progress on them right now.

    This is the metric KEDA's cryo-worker ScaledObject scales on (see
    deploy/helm/cryo/templates/keda-worker-scaledobject.yaml). ADMITTED/RUNNING jobs each
    occupy exactly one worker for their full duration (worker.py's process_next() executes a
    job's entire plan synchronously before claiming another) -- so this count maps directly
    onto "how many worker replicas would let every active job make progress concurrently",
    which is exactly what a 1:1 target value on this metric gives KEDA.

    Regression, 2026-07-31: the previous trigger watched the Redis `cryo:admitted` list's
    *length*, which a worker pops within seconds of admission (long before the job itself
    finishes) -- so the list is back to 0 almost immediately even while every replica stays
    saturated for hours. KEDA saw "0 pending" and never scaled up, then reverted a manual
    scale-up 9 seconds after it was applied ("all metrics below target"), killing a worker
    mid-dispatch and orphaning the job it had just claimed.
    """
    from cryo.db.session import SessionLocal

    with SessionLocal() as session:
        from sqlalchemy import text

        result = session.execute(
            text("SELECT count(*) FROM cryo.jobs WHERE status IN ('admitted', 'running')")
        )
        return int(result.scalar() or 0)


def _run_with_signal(runner: DaemonRunner, *, service_name: str | None = None) -> NoReturn:
    settings = get_settings()
    init_tracing(settings, service_name=service_name or runner.name)
    if service_name and settings.health_port:
        from cryo.observability.health_server import start_health_server

        start_health_server(
            port=settings.health_port,
            service_name=service_name,
            readiness_check=lambda: not runner._stop.is_set(),
            scale_metric_provider=_worker_scale_metric if service_name == "worker" else None,
        )

    def handle_signal(signum: int, frame: object) -> None:
        logger.info("Received signal %d, shutting down...", signum)
        runner.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    runner.run()
    shutdown_tracing()
    sys.exit(0)


def main() -> NoReturn:
    parser = create_parser()
    args = parser.parse_args()

    commands = {
        "governor": run_governor,
        "worker": run_worker,
        "scheduler": run_scheduler,
        "scanner": run_scanner,
        "events": run_events,
        "serve": run_serve,
    }

    handler = commands.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    handler(args)


if __name__ == "__main__":
    main()
