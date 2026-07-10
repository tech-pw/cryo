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
        self._last_silent_loss_sweep: datetime | None = None

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
        self._maybe_silent_data_loss_sweep(current)
        self._dispatch_scheduled_runs(current)
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
        """Per-tick telemetry pipeline. Two stages run on the same tick:

        1. **Fetch** (legacy path): for sources that haven't opted into the per-destination
           curator yet, still poll ``telemetry_trino_url`` and buffer rows to Redis. Cheap
           when there's nothing new; harmless when the URL isn't set.
        2. **Curate**: for every destination with ``query_log_config`` enabled, drain its
           Redis buffer -> Iceberg archive -> curated Postgres -> query_patterns. Failures
           on one destination never block another.
        """
        from datetime import timedelta

        from cryo.config import get_settings

        settings = get_settings()
        if not settings.telemetry_ingest_on_startup:
            return
        if self._last_telemetry_ingest is not None:
            elapsed = current - self._last_telemetry_ingest
            if elapsed < timedelta(hours=1):
                return
        try:
            import asyncio

            from sqlalchemy import select

            from cryo.db.models import Destination, Source
            from cryo.telemetry.iceberg_source import (
                aggregate_from_iceberg_query_log,
                parse_iceberg_query_log_config,
            )

            async def _run() -> tuple[int, int, int, int]:
                total_buffered = 0
                total_curated = 0
                total_patterns_updated = 0
                curators_run = 0
                with SessionLocal() as session:
                    # 1. Legacy fetch path: poll the global Trino URL for any Source that
                    #    hasn't migrated to the per-destination curator yet. Buffers to Redis;
                    #    the curator (or the next curator tick) will handle the rest.
                    sources = session.scalars(select(Source).where(Source.enabled.is_(True))).all()
                    for source in sources:
                        if parse_iceberg_query_log_config(source.config or {}) is not None:
                            total_patterns_updated += aggregate_from_iceberg_query_log(
                                session, source, now=current
                            )
                            continue
                        if not settings.telemetry_trino_url:
                            continue
                        from cryo.telemetry.ingestion import (
                            TrinoQueryIngester,
                            ingest_trino_query_log,
                        )

                        ingester = TrinoQueryIngester(settings.telemetry_trino_url)
                        total_buffered += await ingest_trino_query_log(
                            session,
                            source,
                            ingester,
                            limit=500,
                        )

                    # 2. Curator path: for every destination with a query_log_config block,
                    #    drain the Redis buffer, archive to Iceberg, spill curated to Postgres,
                    #    and aggregate query_patterns. read_through destinations skip the
                    #    archive but still aggregate.
                    destinations = session.scalars(
                        select(Destination).where(Destination.enabled.is_(True))
                    ).all()
                    for destination in destinations:
                        # Bind the destination to a Source. A destination without a Source
                        # link can't spill curated rows (Postgres FK is to sources.id) but
                        # can still archive to Iceberg via the destination's own config.
                        source = _source_for_destination(session, destination)
                        try:
                            from cryo.telemetry.curator import curator_for_destination

                            curator = curator_for_destination(destination, source=source)
                            if curator is None:
                                continue
                            result = await curator.run(session, now=current)
                            curators_run += 1
                            total_curated += result.curated_inserted
                            total_patterns_updated += result.patterns_updated
                            if result.error:
                                import logging

                                logging.getLogger(__name__).warning(
                                    "Curator tick for destination %s partial: %s",
                                    destination.id,
                                    result.error,
                                )
                        except Exception as exc:  # noqa: BLE001
                            import logging

                            logging.getLogger(__name__).warning(
                                "Curator tick for destination %s failed: %s",
                                destination.id,
                                exc,
                            )
                    session.commit()
                return total_buffered, total_curated, total_patterns_updated, curators_run

            buffered, curated, patterns_updated, curators_run = asyncio.run(_run())
            import logging

            logging.getLogger(__name__).info(
                "Telemetry tick: %s buffered, %s curated, %s patterns updated across %s destinations",  # noqa: E501
                buffered,
                curated,
                patterns_updated,
                curators_run,
            )
            if patterns_updated > 0:
                self._generate_layout_recommendations(settings)
            self._last_telemetry_ingest = current
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("Telemetry tick failed: %s", exc)

    def _generate_layout_recommendations(self, settings: Any) -> None:
        """Turn freshly-updated query_patterns into LayoutRecommendation rows.

        generate_layout_recommendation() was fully implemented (column-stats analysis, bloom-
        filter candidates, confidence scoring) but had no caller anywhere in the app -- the
        Optimize page's telemetry existed (query_patterns) but recommendations never did. This
        is the only place that calls it. Ordered so tables with no recommendation yet (NULL
        updated_at) always sort first -- without that, an unordered `LIMIT` could let the same
        already-covered tables win every tick while new ones never get a first pass.
        """
        import logging

        from sqlalchemy import select

        from cryo.db.models import LayoutRecommendation, QueryPattern
        from cryo.optimizer.layout_analyzer import generate_layout_recommendation

        logger = logging.getLogger(__name__)
        with SessionLocal() as session:
            distinct_tables = select(QueryPattern.table_id).distinct().subquery()
            table_ids = (
                session.execute(
                    select(distinct_tables.c.table_id)
                    .outerjoin(
                        LayoutRecommendation,
                        LayoutRecommendation.table_id == distinct_tables.c.table_id,
                    )
                    .order_by(LayoutRecommendation.updated_at.asc().nulls_first())
                    .limit(settings.layout_recommendation_max_tables_per_tick)
                )
                .scalars()
                .all()
            )
            generated = 0
            for table_id in table_ids:
                try:
                    generate_layout_recommendation(session, table_id)
                    generated += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Layout recommendation generation failed for table %s: %s",
                        table_id,
                        exc,
                    )
            if generated:
                logger.info(
                    "Layout recommendations: refreshed %s of %s candidate table(s)",
                    generated,
                    len(table_ids),
                )

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

    def _maybe_silent_data_loss_sweep(self, current: datetime) -> None:
        """Periodically run the silent-data-loss detector for every active table.

        Throttled by ``CRYO_SILENT_LOSS_CHECK_INTERVAL_MIN`` (default 5). Failures on one
        table are isolated -- the sweep must not abort just because one path is unreadable.
        """
        from datetime import timedelta

        from cryo.config import get_settings

        interval_minutes = get_settings().silent_loss_check_interval_min
        if self._last_silent_loss_sweep is not None:
            elapsed = current - self._last_silent_loss_sweep
            if elapsed < timedelta(minutes=interval_minutes):
                return
        try:
            from cryo.scheduler.health import run_silent_data_loss_sweep

            with SessionLocal() as session:
                results = run_silent_data_loss_sweep(session)
            dirty = [r for r in results if not r.report.is_clean()]
            if dirty:
                import logging

                logging.getLogger(__name__).info(
                    "silent_data_loss sweep: %d tables flagged",
                    len(dirty),
                )
            self._last_silent_loss_sweep = current
        except Exception as exc:  # pragma: no cover - defensive: don't break the scheduler.
            import logging

            logging.getLogger(__name__).warning("silent_data_loss sweep failed: %s", exc)

    def _dispatch_scheduled_runs(self, current: datetime) -> None:
        """Enqueue one-off runs whose `scheduled_at` has passed.

        Unlike the `_maybe_*` housekeeping tasks above, this runs every tick with no extra
        throttle -- the check itself is cheap (one indexed query), and a scheduled run should
        fire close to its target time, not be delayed by an internal interval on top of it.
        Both this path and the immediate-API path funnel into the same `queue.enqueue()` ->
        Governor.run_wave() admission flow, so a scheduled run gets the exact same capacity/
        cooldown checks at dispatch time as any other run -- scheduling changes *when* a job
        enters the pending queue, not whether the Governor still gets a say.
        """
        from sqlalchemy import select

        from cryo.db.models import MaintenanceJob, MaintenanceRun

        try:
            with SessionLocal() as session:
                due = session.scalars(
                    select(MaintenanceRun).where(
                        MaintenanceRun.status == "scheduled",
                        MaintenanceRun.scheduled_at.is_not(None),
                        MaintenanceRun.scheduled_at <= current,
                    )
                ).all()
                if not due:
                    return
                for run in due:
                    jobs = session.scalars(
                        select(MaintenanceJob).where(MaintenanceJob.run_id == run.id)
                    ).all()
                    run.status = "queued"
                    session.flush()
                    for job in jobs:
                        self._queue.enqueue(str(job.id), str(job.destination_id))
                session.commit()

            import logging

            logging.getLogger(__name__).info(
                "Dispatched %d scheduled run(s) whose scheduled_at has passed", len(due)
            )
        except Exception as exc:  # pragma: no cover - defensive: don't break the scheduler.
            import logging

            logging.getLogger(__name__).warning("Scheduled-run dispatch failed: %s", exc)


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


def _source_for_destination(session: Any, destination: Any) -> Any:
    """Resolve a Source to bind a destination to for the curator.

    Bootstrap heuristic: pick the first enabled Source in the same tenant as the
    destination. Single-tenant deployments work directly; multi-tenant deployments
    will get a per-destination binding (likely a new FK column) once we add
    multi-engine-per-lakehouse routing. Until then, the curator can still archive
    to Iceberg (no FK) -- only the curated-Postgres spill is gated on having a Source.
    """
    from sqlalchemy import select

    from cryo.db.models import Source

    stmt = select(Source).where(Source.enabled.is_(True))
    if destination.tenant_id is not None:
        stmt = stmt.where(Source.tenant_id == destination.tenant_id)
    return session.scalars(stmt.limit(1)).first()
