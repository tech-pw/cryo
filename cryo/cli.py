#!/usr/bin/env python3
"""Cryo CLI - Entry points for maintenance daemons.

Usage:
    cryo governor [--wave-interval SECS]
    cryo worker [--poll-interval SECS]
    cryo scheduler [--check-interval SECS]
    cryo serve  # runs all three in a single process
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

    # Serve command (all-in-one)
    serve_parser = subparsers.add_parser("serve", help="Run governor, worker, and scheduler")
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


class GovernorRunner(DaemonRunner):
    """Runs the admission governor wave loop."""

    def __init__(self, interval: int = 30, wiring: Any = None) -> None:
        super().__init__("governor", interval)
        self._wiring: Any = wiring
        self._governor: Any = None

    def _tick(self) -> None:
        governor = self._get_governor()
        if governor is None:
            self._logger.warning("Governor not available (missing dependencies)")
            return
        wiring = self._get_wiring()
        if wiring and not wiring.is_governor_leader():
            return

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
                capacity_provider=LiveCapacityProvider(),
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

    def __init__(self, interval: int = 5, wiring: Any = None) -> None:
        super().__init__("scanner", interval)
        self._wiring: Any = wiring
        self._worker: Any = None

    def _tick(self) -> None:
        worker = self._get_worker()
        if worker is None:
            self._logger.warning("Scanner worker not available")
            return
        result = worker.process_next()
        if result is not None and result.run_id:
            self._logger.info("Processed scan run %s: %s", result.run_id[:8], result.status)

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
    from cryo.scheduler.benefit_score_updater import benefit_score_updater_loop_sync

    updater_stop = Event()
    updater_thread = Thread(
        target=benefit_score_updater_loop_sync,
        kwargs={"stop_event": updater_stop},
        name="benefit-score-updater",
        daemon=True,
    )
    updater_thread.start()
    try:
        runner = SchedulerRunner(interval=args.check_interval)
        _run_with_signal(runner, service_name="scheduler")
    finally:
        updater_stop.set()
        updater_thread.join(timeout=5)


def run_events(args: argparse.Namespace) -> NoReturn:
    runner = EventsRunner(interval=args.poll_interval)
    _run_with_signal(runner)


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
    runners = [
        GovernorRunner(interval=args.wave_interval, wiring=wiring),
        WorkerRunner(interval=args.poll_interval, wiring=wiring),
        SchedulerRunner(interval=args.check_interval, wiring=wiring),
        ScannerRunner(interval=args.poll_interval, wiring=wiring),
        BenefitScoreUpdaterRunner(),
    ]
    for runner, (service_name, port) in zip(runners[:4], health_services, strict=True):

        def _readiness(r: DaemonRunner = runner) -> bool:
            return not r._stop.is_set()

        start_health_server(
            port=port,
            service_name=service_name,
            readiness_check=_readiness,
        )

    threads: list[Thread] = []
    for runner in runners:
        thread = Thread(target=runner.run, name=runner.name, daemon=True)
        thread.start()
        threads.append(thread)

    stop = Event()

    def handle_signal(signum: int, frame: object) -> None:
        logger.info("Received signal %d, shutting down...", signum)
        for runner in runners:
            runner.stop()
        stop.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    logger.info("Cryo serve started (governor + worker + scheduler + scanner + benefit scores)")
    while not stop.is_set():
        time.sleep(1)

    for thread in threads:
        thread.join(timeout=5)

    logger.info("Shutdown complete")
    sys.exit(0)


def _run_with_signal(runner: DaemonRunner, *, service_name: str | None = None) -> NoReturn:
    settings = get_settings()
    init_tracing(settings, service_name=service_name or runner.name)
    if service_name and settings.health_port:
        from cryo.observability.health_server import start_health_server

        start_health_server(
            port=settings.health_port,
            service_name=service_name,
            readiness_check=lambda: not runner._stop.is_set(),
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
