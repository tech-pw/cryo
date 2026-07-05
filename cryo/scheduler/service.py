from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from cryo.db.session import SessionLocal
from cryo.planner.benefit_score import update_all_benefit_scores
from cryo.queue.redis_queue import MaintenanceQueue
from cryo.scheduler.adaptive_routing import AdaptiveRoutingContext, resolve_destination_adaptive
from cryo.scheduler.cron import cron_is_due
from cryo.scheduler.routing import build_destinations_index, resolve_destination
from cryo.scheduler.rules import PresetConfig, PresetRule, TableAssignment, TableCandidate
from cryo.scheduler.scheduler import ScheduledRunDraft, build_scheduled_run, enqueue_run


class SchedulerStore(Protocol):
    def list_due_presets(self, now: datetime) -> list[tuple[PresetConfig, datetime | None]]: ...

    def list_tables(self) -> list[TableCandidate]: ...

    def list_presets(self) -> list[PresetConfig]: ...

    def list_rules(self) -> list[PresetRule]: ...

    def list_assignments(self) -> list[TableAssignment]: ...

    def list_destinations(self) -> list[dict[str, Any]]: ...

    def persist_run(self, run: ScheduledRunDraft) -> None: ...

    def mark_preset_run(self, preset_id: str, run_at: datetime) -> None: ...


@dataclass(frozen=True)
class SchedulerTickResult:
    runs_created: int = 0
    jobs_enqueued: int = 0
    preset_ids: tuple[str, ...] = ()


class SchedulerService:
    def __init__(
        self,
        store: SchedulerStore,
        queue: MaintenanceQueue,
        *,
        leader_check: Callable[[], bool] | None = None,
        benefit_score_update_interval_hours: int = 4,
    ) -> None:
        self._store = store
        self._queue = queue
        self._leader_check = leader_check or (lambda: True)
        self._benefit_score_interval_hours = benefit_score_update_interval_hours
        self._last_benefit_score_update: datetime | None = None
        self._last_ml_drift_check: datetime | None = None
        self._last_telemetry_ingest: datetime | None = None
        self._last_query_log_purge: datetime | None = None

    def tick(self, *, now: datetime | None = None) -> SchedulerTickResult:
        if not self._leader_check():
            return SchedulerTickResult()

        current = now or datetime.now(UTC)

        self._expire_stale_approval_requests()

        # Update benefit scores periodically (helps AutoComp prioritization)
        self._maybe_update_benefit_scores(current)
        self._maybe_check_ml_drift(current)
        self._maybe_ingest_telemetry(current)
        self._maybe_purge_query_logs(current)
        due_presets = self._store.list_due_presets(current)
        if not due_presets:
            return SchedulerTickResult()

        tables = self._store.list_tables()
        presets = self._store.list_presets()
        rules = self._store.list_rules()
        assignments = self._store.list_assignments()
        destinations = build_destinations_index(self._store.list_destinations())

        runs_created = 0
        jobs_enqueued = 0
        fired: list[str] = []

        for preset, last_run_at in due_presets:
            if not cron_is_due(preset.schedule_cron, now=current, last_run_at=last_run_at):
                continue

            run = build_scheduled_run(
                preset,
                tables,
                presets,
                rules,
                assignments,
                trigger="cron",
                now=current,
            )
            if not run.jobs:
                self._store.mark_preset_run(preset.id, current)
                continue

            routed_jobs = _apply_routing(run, preset, tables, destinations)
            self._store.persist_run(routed_jobs)
            enqueue_run(routed_jobs, self._queue)
            self._store.mark_preset_run(preset.id, current)

            runs_created += 1
            jobs_enqueued += len(routed_jobs.jobs)
            fired.append(preset.id)

        return SchedulerTickResult(
            runs_created=runs_created,
            jobs_enqueued=jobs_enqueued,
            preset_ids=tuple(fired),
        )

    def _expire_stale_approval_requests(self) -> None:
        try:
            from cryo.workflows.approvals import expire_stale_approval_requests

            with SessionLocal() as session:
                expired = expire_stale_approval_requests(session)
                if expired:
                    import logging

                    logging.getLogger(__name__).info("Expired %s stale approval requests", expired)
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning("Failed to expire stale approval requests: %s", e)

    def _maybe_update_benefit_scores(self, current: datetime) -> None:
        """Update partition benefit scores periodically for AutoComp prioritization."""
        from datetime import timedelta

        if self._last_benefit_score_update is None:
            should_update = True
        else:
            elapsed = current - self._last_benefit_score_update
            should_update = elapsed >= timedelta(hours=self._benefit_score_interval_hours)

        if should_update:
            try:
                with SessionLocal() as session:
                    updated = update_all_benefit_scores(session)
                    import logging

                    logging.getLogger(__name__).info(
                        f"Updated benefit scores for {updated} partitions"
                    )
                self._last_benefit_score_update = current
            except Exception as e:
                import logging

                logging.getLogger(__name__).warning(f"Failed to update benefit scores: {e}")

    def _maybe_check_ml_drift(self, current: datetime) -> None:
        from datetime import timedelta

        from cryo.config import get_settings

        interval_hours = get_settings().ml_drift_check_interval_hours
        if self._last_ml_drift_check is not None:
            elapsed = current - self._last_ml_drift_check
            if elapsed < timedelta(hours=interval_hours):
                return
        try:
            from cryo.ml.anomaly_detector import detect_all_anomalies
            from cryo.ml.health_classifier import HealthLabel, evaluate_classifier

            with SessionLocal() as session:
                anomalies = detect_all_anomalies(session, min_severity="high")
                drift_report: dict[str, object] = {"anomalies": len(anomalies)}
                from sqlalchemy import select

                from cryo.db.models import Table, TableStats

                rows = session.execute(
                    select(Table, TableStats)
                    .join(TableStats, TableStats.table_id == Table.id)
                    .limit(200)
                ).all()
                labeled: list[HealthLabel] = []
                stats_by_table: dict[str, dict[str, object]] = {}
                for table, stats in rows:
                    if stats is None:
                        continue
                    tid = str(table.id)
                    stats_dict = {
                        "table_id": tid,
                        "total_files": stats.total_files,
                        "small_files_count": stats.small_files_count,
                        "overall_score": stats.overall_score,
                        "delete_file_pct": (stats.raw or {}).get("delete_file_pct", 0)
                        if stats.raw
                        else 0,
                    }
                    stats_by_table[tid] = stats_dict
                    needs = (
                        float(stats.small_files_count or 0) > 500
                        or float(stats.overall_score or 100) < 50
                    )
                    labeled.append(HealthLabel(table_id=tid, needs_emergency_compaction=needs))
                if len(labeled) >= 4:
                    eval_result = evaluate_classifier(labeled, stats_by_table)
                    drift_report["classifier"] = eval_result
                    if not eval_result.get("beats_threshold"):
                        import logging

                        logging.getLogger(__name__).warning(
                            "Health classifier drift: accuracy below threshold baseline %s",
                            eval_result,
                        )
                session.commit()
            import logging

            logging.getLogger(__name__).info("Scheduled ML drift scan: %s", drift_report)
            self._last_ml_drift_check = current
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("ML drift check failed: %s", exc)

    def _maybe_ingest_telemetry(self, current: datetime) -> None:
        from datetime import timedelta

        from cryo.config import get_settings

        settings = get_settings()
        if not settings.telemetry_trino_url:
            return
        if not settings.telemetry_ingest_on_startup:
            return
        if self._last_telemetry_ingest is not None:
            elapsed = current - self._last_telemetry_ingest
            if elapsed < timedelta(hours=1):
                return
        try:
            import asyncio

            from sqlalchemy import select

            from cryo.db.models import Source
            from cryo.telemetry.ingestion import TrinoQueryIngester, ingest_trino_query_log
            from cryo.telemetry.patterns import aggregate_query_patterns

            ingester = TrinoQueryIngester(settings.telemetry_trino_url)

            async def _run() -> tuple[int, int]:
                total_ingested = 0
                total_patterns_updated = 0
                with SessionLocal() as session:
                    sources = session.scalars(select(Source).where(Source.enabled.is_(True))).all()
                    for source in sources:
                        count = await ingest_trino_query_log(
                            session,
                            source,
                            ingester,
                            limit=500,
                        )
                        total_ingested += count
                        # Aggregate immediately so query_patterns (what partition/layout
                        # recommendations actually read) reflects this tick's data -- without
                        # this, the scheduled path only ever wrote raw query_logs rows and
                        # relied on someone manually re-triggering ingestion via the UI to
                        # get patterns updated at all.
                        total_patterns_updated += aggregate_query_patterns(session, table_id=None)
                    session.commit()
                return total_ingested, total_patterns_updated

            ingested, patterns_updated = asyncio.run(_run())
            import logging

            logging.getLogger(__name__).info(
                "Telemetry ingest tick: %s query logs, %s patterns updated",
                ingested,
                patterns_updated,
            )
            self._last_telemetry_ingest = current
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("Telemetry ingest tick failed: %s", exc)

    def _maybe_purge_query_logs(self, current: datetime) -> None:
        from datetime import timedelta

        from cryo.config import get_settings

        if self._last_query_log_purge is not None:
            elapsed = current - self._last_query_log_purge
            if elapsed < timedelta(days=1):
                return
        try:
            from cryo.telemetry.patterns import purge_old_query_logs

            settings = get_settings()
            with SessionLocal() as session:
                deleted = purge_old_query_logs(session, settings.query_log_retention_days)
            import logging

            logging.getLogger(__name__).info("Query log purge tick: %s rows deleted", deleted)
            self._last_query_log_purge = current
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("Query log purge tick failed: %s", exc)


def _apply_routing(
    run: ScheduledRunDraft,
    preset: PresetConfig,
    tables: list[TableCandidate],
    destinations: dict[str, dict[str, Any]],
) -> ScheduledRunDraft:
    tables_by_id = {table.id: table for table in tables}
    routed = []
    for job in run.jobs:
        table = tables_by_id.get(job.table_id)
        destination_id = job.destination_id
        if table is not None:
            if preset.guardrails.get("adaptive_routing"):
                route = resolve_destination_adaptive(
                    table,
                    preset,
                    destinations,
                    AdaptiveRoutingContext(),
                )
            else:
                route = resolve_destination(table, preset, destinations)
            destination_id = route.destination_id
        routed.append(
            type(job)(
                id=job.id,
                run_id=job.run_id,
                table_id=job.table_id,
                destination_id=destination_id,
                mode=job.mode,
                status=job.status,
            )
        )
    return ScheduledRunDraft(
        id=run.id,
        preset_id=run.preset_id,
        trigger=run.trigger,
        deadline_at=run.deadline_at,
        jobs=tuple(routed),
    )


class InMemorySchedulerStore:
    """In-memory scheduler store for tests and local CLI."""

    def __init__(self) -> None:
        self.presets: list[PresetConfig] = []
        self.rules: list[PresetRule] = []
        self.assignments: list[TableAssignment] = []
        self.tables: list[TableCandidate] = []
        self.destinations: list[dict[str, Any]] = []
        self.last_runs: dict[str, datetime] = {}
        self.runs: list[ScheduledRunDraft] = []

    def list_due_presets(self, now: datetime) -> list[tuple[PresetConfig, datetime | None]]:
        del now
        return [
            (preset, self.last_runs.get(preset.id))
            for preset in self.presets
            if preset.enabled and preset.schedule_cron
        ]

    def list_tables(self) -> list[TableCandidate]:
        return list(self.tables)

    def list_presets(self) -> list[PresetConfig]:
        return list(self.presets)

    def list_rules(self) -> list[PresetRule]:
        return list(self.rules)

    def list_assignments(self) -> list[TableAssignment]:
        return list(self.assignments)

    def list_destinations(self) -> list[dict[str, Any]]:
        return list(self.destinations)

    def persist_run(self, run: ScheduledRunDraft) -> None:
        self.runs.append(run)

    def mark_preset_run(self, preset_id: str, run_at: datetime) -> None:
        self.last_runs[preset_id] = run_at


def new_job_id() -> str:
    return str(uuid4())
