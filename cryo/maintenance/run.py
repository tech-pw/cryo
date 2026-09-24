"""Batch maintenance CronJob entrypoint: ``python -m cryo.maintenance.run``.

--dry-run            select and plan only; print lanes and statement counts, touch nothing
--limit N            override CRYO_MAINTENANCE_TOP_N for this run
--table-ids a,b,...  run exactly these tables (ad-hoc); cooldown and eligibility are skipped
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import uuid
from collections.abc import Sequence

from cryo.config import get_settings
from cryo.db.session import SessionLocal
from cryo.maintenance.coordinator import CRON_TRIGGER, BatchMaintenance, RunReport
from cryo.observability.logging import quiet_third_party_loggers
from cryo.queue.locks import LockStore

logger = logging.getLogger(__name__)


def _parse_ids(raw: str | None) -> list[uuid.UUID] | None:
    if not raw:
        return None
    return [uuid.UUID(part.strip()) for part in raw.split(",") if part.strip()]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one batch Iceberg maintenance pass")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--table-ids", default=None, help="Comma-separated table UUIDs")
    parser.add_argument("--trigger", default=None, help="Recorded on the run row")
    return parser.parse_args(argv)


def _rescan(table_ids: Sequence[uuid.UUID]) -> int:
    from cryo.scanner.run import run_scan

    with SessionLocal() as session:
        # num_chunks=1: run_scan otherwise defaults to chunk 0 of scan_num_chunks and would
        # silently rescan only a fraction of the maintained tables.
        summary = run_scan(session, table_ids=list(table_ids), num_chunks=1, full_scan=True)
    logger.info("Post-maintenance rescan: %s", summary)
    return summary.scanned


def _exit_on_sigterm(signum: int, _frame: object) -> None:
    # Python's default SIGTERM action kills the process without unwinding, which would skip
    # the coordinator's teardown and leave lane pods, Secrets and running jobs behind.
    raise SystemExit(128 + signum)


def main(argv: Sequence[str] | None = None) -> int:
    signal.signal(signal.SIGTERM, _exit_on_sigterm)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    quiet_third_party_loggers()
    args = parse_args(argv)
    settings = get_settings()
    table_ids = _parse_ids(args.table_ids)
    trigger = args.trigger or ("batch-adhoc" if table_ids else CRON_TRIGGER)

    lock_store: LockStore
    if args.dry_run:
        from cryo.queue.locks import InMemoryLockStore

        lock_store = InMemoryLockStore()
    else:
        from cryo.infra.wiring import build_runtime_wiring

        lock_store = build_runtime_wiring(settings).lock_store

    def notify(report: RunReport) -> bool:
        from cryo.maintenance.report import post_report

        with SessionLocal() as session:
            return post_report(session, report, web_base_url=settings.web_app_base_url)

    coordinator = BatchMaintenance(
        session_factory=SessionLocal,
        settings=settings,
        lock_store=lock_store,
        rescan=_rescan,
        notify=notify,
    )
    report = coordinator.run(
        trigger=trigger, table_ids=table_ids, limit=args.limit, dry_run=args.dry_run
    )
    print(
        json.dumps(
            {
                "run_id": str(report.run_id),
                "dry_run": report.dry_run,
                **report.to_summary(),
                "planned_statements": report.planned_statements,
            },
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
