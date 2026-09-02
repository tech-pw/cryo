from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from cryo.db.session import SessionLocal
from cryo.queue.redis_queue import MaintenanceQueue
from cryo.scheduler.adaptive_routing import AdaptiveRoutingContext, resolve_destination_adaptive
from cryo.scheduler.cron import cron_is_due
from cryo.scheduler.routing import build_destinations_index, resolve_destination
from cryo.scheduler.rules import PresetConfig, PresetRule, TableAssignment, TableCandidate
from cryo.scheduler.scheduler import (
    ScheduledRunDraft,
    build_scheduled_run,
    enqueue_run,
    fifo_priority,
)

# _maybe_check_ml_drift's own body (detect_all_anomalies) is bounded by this -- see that
# method's own comment for the incident. A dedicated single-worker pool, isolated from
# scanner.run.py's and layout_analyzer.py's own executors (different subsystem, different
# call volume -- this only ever needs to bound one call at a time, from tick()'s own thread).
_ML_DRIFT_CHECK_TIMEOUT_SECONDS = 60.0
_ml_drift_executor: ThreadPoolExecutor | None = None


def _ml_drift_check_executor() -> ThreadPoolExecutor:
    global _ml_drift_executor
    if _ml_drift_executor is None:
        _ml_drift_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ml-drift-check")
    return _ml_drift_executor


def _run_ml_drift_scan() -> dict[str, object]:
    """The actual scan body, unchanged in behavior from before the 2026-08-31 timeout fix --
    only extracted out of _maybe_check_ml_drift so it can run on the bounded executor thread
    (see that method's own docstring for why). Opens its own session since it now runs on a
    separate thread from tick()'s caller."""
    from sqlalchemy import select

    from cryo.db.models import Table, TableStats
    from cryo.ml.anomaly_detector import detect_all_anomalies
    from cryo.ml.health_classifier import HealthLabel, evaluate_classifier

    with SessionLocal() as session:
        anomalies = detect_all_anomalies(session, min_severity="high")
        drift_report: dict[str, object] = {"anomalies": len(anomalies)}

        rows = session.execute(
            select(Table, TableStats).join(TableStats, TableStats.table_id == Table.id).limit(200)
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
                "delete_file_pct": (stats.raw or {}).get("delete_file_pct", 0) if stats.raw else 0,
            }
            stats_by_table[tid] = stats_dict
            needs = (
                float(stats.small_files_count or 0) > 500 or float(stats.overall_score or 100) < 50
            )
            labeled.append(HealthLabel(table_id=tid, needs_emergency_compaction=needs))
        if len(labeled) >= 4:
            eval_result = evaluate_classifier(labeled, stats_by_table)
            drift_report["classifier"] = eval_result
            if not eval_result.get("beats_threshold"):
                import logging

                logging.getLogger(__name__).warning(
                    "Health classifier drift: accuracy below threshold baseline %s", eval_result
                )
        session.commit()
    return drift_report


class SchedulerStore(Protocol):
    def list_due_presets(self, now: datetime) -> list[tuple[PresetConfig, datetime | None]]: ...

    def list_tables(self) -> list[TableCandidate]: ...

    def list_presets(self) -> list[PresetConfig]: ...

    def list_rules(self) -> list[PresetRule]: ...

    def list_assignments(self) -> list[TableAssignment]: ...

    def list_destinations(self) -> list[dict[str, Any]]: ...

    def consecutive_engine_failures(
        self, table_ids: Sequence[str]
    ) -> dict[str, dict[str, int]]: ...

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
    ) -> None:
        self._store = store
        self._queue = queue
        self._leader_check = leader_check or (lambda: True)
        self._last_ml_drift_check: datetime | None = None
        self._last_telemetry_ingest: datetime | None = None
        self._last_query_log_purge: datetime | None = None
        self._last_audit_log_prune: datetime | None = None
        self._last_rollout_safety_check: datetime | None = None
        self._last_silent_loss_sweep: datetime | None = None
        self._silent_loss_cursor: Any | None = None
        self._last_unmatched_tables_check: datetime | None = None
        self._last_cost_ceiling_check: datetime | None = None
        self._last_dark_table_detection: datetime | None = None
        self._last_cost_attribution: datetime | None = None
        self._last_trino_cost_pricing: datetime | None = None

    def tick(self, *, now: datetime | None = None) -> SchedulerTickResult:
        """One scheduler cycle: dispatch real maintenance work first, diagnostics/telemetry
        housekeeping after.

        Ordering here is deliberate, not incidental -- found live, 2026-08-31, following the
        _maybe_check_ml_drift incident (see that method's own docstring): even after bounding
        that one step, dispatch was still reachable only after up to ~25-30min of *other*
        unbounded, sequential housekeeping (a full first-run telemetry/query-log catch-up being
        the single biggest contributor observed live -- tens of thousands of buffered Metabase
        query statements to parse through _maybe_ingest_telemetry). None of that housekeeping
        writes anything the dispatch path below reads -- they're independent side-effecting
        tasks that happened to share this synchronous call sequence purely by original code
        organization, not by a real dependency -- so there was no correctness reason for
        dispatch to wait behind them. Worse, every pod restart (deploys, node reschedules, both
        frequent in practice) resets every _last_* in-memory timestamp gating these tasks,
        re-paying the full first-run cost each time and pushing dispatch back by that same
        20-30min window on every restart.

        Moving the actual dispatch (_dispatch_scheduled_runs + the due-preset loop) to run
        immediately after the leader check means a fresh pod (or a pod that's been blocked for
        days, the original incident's shape) reaches real maintenance dispatch on its very
        first tick, in the time _dispatch_due_presets itself takes -- typically seconds -- not
        after clearing 11 other steps first. The housekeeping steps still run every tick, just
        after dispatch instead of before it; their own individual cadence gating
        (_last_ml_drift_check etc.) and bounds (where added, see _maybe_check_ml_drift) are
        unchanged.
        """
        if not self._leader_check():
            return SchedulerTickResult()

        current = now or datetime.now(UTC)

        self._dispatch_scheduled_runs(current)
        result = self._dispatch_due_presets(current)

        self._expire_stale_approval_requests()

        self._maybe_check_ml_drift(current)
        self._maybe_ingest_telemetry(current)
        self._maybe_purge_query_logs(current)
        self._maybe_prune_audit_logs(current)
        self._maybe_check_rollout_safety(current)
        self._maybe_silent_data_loss_sweep(current)
        self._maybe_check_unmatched_tables(current)
        self._maybe_check_cost_ceiling(current)
        self._maybe_detect_dark_tables(current)
        self._maybe_aggregate_cost_attribution(current)
        self._maybe_price_trino_scan_cost(current)
        self._sweep_pending_fallback_dispatches()

        return result

    def _dispatch_due_presets(self, current: datetime) -> SchedulerTickResult:
        """The actual cron-preset dispatch loop, extracted out of tick() so it can run first
        -- see tick()'s own docstring for why. Behavior unchanged from before that reordering."""
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

            failure_history: dict[str, dict[str, int]] = {}
            if preset.guardrails.get("adaptive_routing"):
                get_failures = getattr(self._store, "consecutive_engine_failures", None)
                if get_failures is not None:
                    failure_history = get_failures([job.table_id for job in run.jobs])
            routed_jobs = _apply_routing(
                run, preset, tables, destinations, failure_history=failure_history
            )
            self._store.persist_run(routed_jobs)
            # created_at=current (the tick's own now, not a fresh datetime.now() call) so
            # this run's fifo_priority reflects its real logical creation time -- see
            # fifo_priority's own docstring for why every enqueue call site defaulting to
            # priority=0 left cross-run admission order effectively random by job UUID.
            enqueue_run(routed_jobs, self._queue, created_at=current)
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

    def _maybe_check_ml_drift(self, current: datetime) -> None:
        """Runs the ML-drift diagnostic scan on ``ml_drift_check_interval_hours`` cadence,
        bounded by ``_ML_DRIFT_CHECK_TIMEOUT_SECONDS``.

        Found live, 2026-08-31: ``detect_all_anomalies`` runs 2+ unbatched, sequential DB
        queries per table with recent scan history (4,287 tables observed live) -- fine at a
        smaller fleet size, but past ~5 minutes at this scale, with no timeout of its own. Since
        this is the *first* step in tick() (called before ``_dispatch_scheduled_runs``), and
        ``self._last_ml_drift_check`` only gets set *after* a successful completion, a single
        slow/hung call here meant it never updated -- so every subsequent tick re-attempted the
        same expensive scan from scratch, forever, never reaching dispatch even once. Confirmed
        live: zero new MaintenanceRun rows created for days despite the scheduler pod staying
        healthy and cron_is_due (fixed earlier the same day) correctly reporting every preset as
        due on every single tick -- the loop never got that far.

        Bounding this call (same ThreadPoolExecutor + future.result(timeout=...) pattern already
        proven in scanner/run.py and layout_analyzer.py for the identical shape of problem) and
        -- critically -- still recording ``_last_ml_drift_check`` on timeout, not just success,
        so a slow/failing scan degrades to "skipped this cycle" rather than "silently blocks
        every tick forever." The scan itself is a best-effort diagnostic (already wrapped in its
        own try/except, logs a warning and moves on) -- skipping one cycle costs nothing that
        blocking dispatch doesn't cost far more.
        """
        from datetime import timedelta

        from cryo.config import get_settings

        interval_hours = get_settings().ml_drift_check_interval_hours
        if self._last_ml_drift_check is not None:
            elapsed = current - self._last_ml_drift_check
            if elapsed < timedelta(hours=interval_hours):
                return

        import logging

        logger = logging.getLogger(__name__)
        future = _ml_drift_check_executor().submit(_run_ml_drift_scan)
        try:
            drift_report = future.result(timeout=_ML_DRIFT_CHECK_TIMEOUT_SECONDS)
            logger.info("Scheduled ML drift scan: %s", drift_report)
        except FuturesTimeoutError:
            logger.warning(
                "ML drift check exceeded %.0fs and was abandoned -- skipping this cycle "
                "(the abandoned scan keeps running in the background and its result is "
                "discarded if it eventually completes)",
                _ML_DRIFT_CHECK_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.warning("ML drift check failed: %s", exc)
        finally:
            # Set on timeout/failure too, not just success -- see this method's own docstring
            # for why: the alternative is re-attempting a slow/hanging scan every single tick.
            self._last_ml_drift_check = current

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

                        ingester = TrinoQueryIngester(
                            settings.telemetry_trino_url,
                            user=settings.telemetry_trino_user or "cryo",
                            password=settings.telemetry_trino_password,
                            verify_ssl=settings.telemetry_trino_ssl_verify,
                        )
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
            if settings.property_autopilot_enabled:
                self._run_property_autopilot(settings)
            if settings.fast_path_check_enabled:
                self._run_fast_path_check(settings)
            if settings.layout_autopilot_enabled:
                self._run_layout_autopilot(settings)
            self._measure_layout_recommendation_effectiveness(settings)
            self._last_telemetry_ingest = current
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("Telemetry tick failed: %s", exc)

    def _run_fast_path_check(self, settings: Any) -> None:
        """Detect small-file / missing target-file-size on newly discovered tables.

        Detection only — Feature 3 scheduling is calling compute_property_recommendations
        here, not applying properties (that remains property_autopilot, still opt-in).
        """
        import logging

        from cryo.scanner.fast_path_check import run_fast_path_check

        logger = logging.getLogger(__name__)
        with SessionLocal() as session:
            findings = run_fast_path_check(
                session, max_tables=settings.fast_path_check_max_tables_per_tick
            )
        if findings:
            logger.info("Fast-path check tick: %s finding(s)", len(findings))

    def _run_property_autopilot(self, settings: Any) -> None:
        """Apply each active table's safe (tier1, non-risky) property recommendations.

        Off by default (property_autopilot_enabled) -- see cryo/scanner/property_autopilot.py
        for the full safety rationale. Runs every tick regardless of patterns_updated (unlike
        _generate_layout_recommendations) since a table's tier1 recommendations can change
        from a fresh scan alone, without a new query pattern.
        """
        import logging

        from cryo.scanner.property_autopilot import run_property_autopilot

        logger = logging.getLogger(__name__)
        with SessionLocal() as session:
            result = run_property_autopilot(
                session, max_tables=settings.property_autopilot_max_tables_per_tick
            )
        if result.tables_changed or result.tables_failed:
            logger.info(
                "Property autopilot tick: %s/%s tables changed, %s failed",
                result.tables_changed,
                result.tables_considered,
                result.tables_failed,
            )

    def _run_layout_autopilot(self, settings: Any) -> None:
        """Auto-approve and execute pending sort/z-order recommendations, but only for tables
        whose effective preset explicitly opts in via params.auto_apply_layout_recommendations.

        Closes the gap between "make it a part of preset" and the reality that link_layout is
        a real Spark data rewrite (tier2), not a metadata-only flip -- see
        cryo/scanner/property_autopilot.py's tier2 rationale, which is exactly why this stays
        gated per-preset rather than a single fleet-wide switch like property_autopilot_enabled.
        A preset owner decides "yes, always resort tables assigned to me" once; individual
        tables still don't need a human clicking approve/execute in the UI every time a fresh
        recommendation lands.

        Reuses execute_layout_recommendation() itself (not a reimplementation) so this gets the
        exact same Spark rewrite + write.sort-order persistence + intent/effect-budget tracking
        as the manual API path -- see cryo/api/routes_smart_maintenance.py.
        """
        import logging

        from sqlalchemy import select

        from cryo.api.routes_smart_maintenance import (
            ExecuteLayoutRecommendationRequest,
            execute_layout_recommendation,
        )
        from cryo.api.security import Principal, Role
        from cryo.db.models import LayoutRecommendation, Table
        from cryo.db.repositories import presets as presets_repo

        logger = logging.getLogger(__name__)
        autopilot_principal = Principal(
            subject="cryo-layout-autopilot", roles=frozenset({Role.OPERATOR})
        )
        applied = 0
        failed = 0
        with SessionLocal() as session:
            candidates = session.execute(
                select(LayoutRecommendation)
                .where(LayoutRecommendation.status == "pending")
                .where(LayoutRecommendation.recommended_strategy.in_(("sort", "zorder")))
                .order_by(LayoutRecommendation.updated_at.asc().nulls_first())
                .limit(settings.layout_autopilot_max_tables_per_tick)
            ).scalars().all()
            for recommendation in candidates:
                table = session.get(Table, recommendation.table_id)
                if table is None:
                    continue
                effective = presets_repo.resolve_effective_preset(session, table)
                if effective is None:
                    continue
                auto_apply = (effective.preset.params or {}).get(
                    "auto_apply_layout_recommendations"
                )
                if not bool(auto_apply):
                    continue
                recommendation.status = "approved"
                recommendation.approved_at = datetime.now(UTC)
                recommendation.approved_by = autopilot_principal.subject
                session.commit()
                try:
                    execute_layout_recommendation(
                        recommendation.id,
                        ExecuteLayoutRecommendationRequest(),
                        session,
                        autopilot_principal,
                    )
                    applied += 1
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    logger.warning(
                        "Layout autopilot: auto-execute failed for recommendation %s "
                        "(table %s.%s): %s",
                        recommendation.id,
                        table.schema_name,
                        table.table_name,
                        exc,
                    )
        if applied or failed:
            logger.info(
                "Layout autopilot tick: %s applied, %s failed",
                applied,
                failed,
            )

    def _generate_layout_recommendations(self, settings: Any) -> None:
        """Turn freshly-updated query_patterns into LayoutRecommendation rows.

        generate_layout_recommendation() was fully implemented (column-stats analysis, bloom-
        filter candidates, confidence scoring) but had no caller anywhere in the app -- the
        Optimize page's telemetry existed (query_patterns) but recommendations never did. This
        is the only place that calls it. Ordered so tables with no recommendation yet (NULL
        updated_at) always sort first -- without that, an unordered `LIMIT` could let the same
        already-covered tables win every tick while new ones never get a first pass.

        Excludes ``status == "applied"`` rows (PDAE-8288): generate_layout_recommendation
        overwrites current_prune_efficiency/recommended_strategy/etc in place on any table it's
        called for, with no status check -- an applied recommendation regenerated by a routine
        refresh would silently look "pending again" in every field except status itself, and
        would corrupt the frozen applied_prune_efficiency_baseline's meaning by moving the
        *current* efficiency out from under measure_layout_recommendation_effectiveness's own
        fresh read. A resolved recommendation needs a deliberate new decision to revisit, not an
        implicit overwrite from routine telemetry.
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
                    .where(LayoutRecommendation.status.is_distinct_from("applied"))
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

    def _measure_layout_recommendation_effectiveness(self, settings: Any) -> None:
        """Fill in observed_prune_improvement for applied sort/z-order recommendations that
        haven't been measured yet (PDAE-8288) -- the column existed and was read into every API
        response, but nothing ever wrote it, so a rewrite's real before/after effect was never
        confirmed. Runs every telemetry tick regardless of patterns_updated (unlike
        _generate_layout_recommendations): an applied recommendation just needs enough real
        wall-clock time to pass, not new query pattern data, before it's worth re-measuring.
        """
        import logging
        from datetime import timedelta

        from sqlalchemy import select

        from cryo.db.models import LayoutRecommendation
        from cryo.optimizer.layout_analyzer import (
            MIN_HOURS_BEFORE_MEASURING_EFFECTIVENESS,
            measure_layout_recommendation_effectiveness,
        )

        logger = logging.getLogger(__name__)
        cutoff = datetime.now(UTC) - timedelta(hours=MIN_HOURS_BEFORE_MEASURING_EFFECTIVENESS)
        measured = 0
        failed = 0
        with SessionLocal() as session:
            candidates = (
                session.execute(
                    select(LayoutRecommendation)
                    .where(LayoutRecommendation.status == "applied")
                    .where(LayoutRecommendation.observed_prune_improvement.is_(None))
                    .where(LayoutRecommendation.applied_at.isnot(None))
                    .where(LayoutRecommendation.applied_at <= cutoff)
                    .order_by(LayoutRecommendation.applied_at.asc())
                    .limit(settings.layout_effectiveness_max_tables_per_tick)
                )
                .scalars()
                .all()
            )
            for recommendation in candidates:
                try:
                    result = measure_layout_recommendation_effectiveness(session, recommendation)
                    if result is not None:
                        measured += 1
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    logger.warning(
                        "Layout effectiveness measurement failed for recommendation %s: %s",
                        recommendation.id,
                        exc,
                    )
        if measured or failed:
            logger.info(
                "Layout effectiveness measurement tick: %s measured, %s failed",
                measured,
                failed,
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

    def _maybe_prune_audit_logs(self, current: datetime) -> None:
        from datetime import timedelta

        if self._last_audit_log_prune is not None:
            if current - self._last_audit_log_prune < timedelta(days=1):
                return

        try:
            from cryo.config import get_settings
            from cryo.db.repositories.audit_retention import prune_audit_logs
            from cryo.observability.metrics import (
                audit_retention_backlog,
                audit_retention_deleted_total,
            )

            settings = get_settings()
            with SessionLocal() as session:
                result = prune_audit_logs(
                    session,
                    settings.audit_log_retention_days,
                    now=current,
                    batch_size=settings.audit_log_prune_batch_size,
                    max_rows=settings.audit_log_prune_max_rows_per_tick,
                )
            audit_retention_deleted_total.inc(result.deleted)
            audit_retention_backlog.set(result.remaining)

            import logging

            logging.getLogger(__name__).info(
                "Audit retention tick: %s rows deleted, %s expired rows remain",
                result.deleted,
                result.remaining,
            )
            self._last_audit_log_prune = current
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("Audit retention tick failed: %s", exc)

    def _maybe_detect_dark_tables(self, current: datetime) -> None:
        """Once a day, advance every active table's consecutive-zero-query-window counter.

        Must be daily, not every tick (the telemetry tick this file also runs can fire hourly)
        -- cryo.telemetry.dark_tables.DARK_TABLE_CONFIRMATION_WINDOWS=3 is meant to mean "3
        distinct days of a silent trailing-30-day window", not "3 ticks a few hours apart",
        or a table could get falsely confirmed dark within a single afternoon.
        """
        from datetime import timedelta

        if self._last_dark_table_detection is not None:
            if current - self._last_dark_table_detection < timedelta(days=1):
                return

        try:
            from cryo.telemetry.dark_tables import detect_dark_table_candidates

            with SessionLocal() as session:
                result = detect_dark_table_candidates(session, now=current)

            import logging

            logging.getLogger(__name__).info(
                "Dark table detection tick: checked=%s newly_zero=%s confirmed_this_run=%s "
                "reset_to_active=%s",
                result.checked,
                result.newly_zero,
                result.confirmed_this_run,
                result.reset_to_active,
            )
            self._last_dark_table_detection = current
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("Dark table detection tick failed: %s", exc)

    def _maybe_aggregate_cost_attribution(self, current: datetime) -> None:
        """Once a day, roll up real Trino query-log scan bytes by (day, client, schema) into
        cryo.query_cost_attribution_daily for every source with an Iceberg-native query-log
        configured. Daily (not the hourly telemetry tick) -- this is a cost *report*, not a
        latency-sensitive signal, and each run does real Iceberg catalog scans per source.
        """
        from datetime import timedelta

        if self._last_cost_attribution is not None:
            if current - self._last_cost_attribution < timedelta(days=1):
                return

        try:
            import logging

            from sqlalchemy import select

            from cryo.db.models import Source
            from cryo.telemetry.cost_attribution import aggregate_query_cost_attribution
            from cryo.telemetry.iceberg_source import parse_iceberg_query_log_config

            logger = logging.getLogger(__name__)
            total_windows = 0
            total_rows = 0
            with SessionLocal() as session:
                sources = session.scalars(select(Source).where(Source.enabled.is_(True))).all()
                for source in sources:
                    if parse_iceberg_query_log_config(source.config or {}) is None:
                        continue
                    try:
                        result = aggregate_query_cost_attribution(session, source, now=current)
                        total_windows += result.windows_processed
                        total_rows += result.rows_upserted
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Cost attribution tick failed for source %s: %s", source.id, exc
                        )

            logger.info(
                "Cost attribution tick: %s window(s) across sources, %s rows upserted",
                total_windows,
                total_rows,
            )
            self._last_cost_attribution = current
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("Cost attribution tick failed: %s", exc)

    def _maybe_price_trino_scan_cost(self, current: datetime) -> None:
        """Once a day, derive a real $/GiB Trino compute price (trino-db namespace's actual
        Grafana Athena CUR cost, per cryo.reporting.cost_ceiling, divided by real bytes scanned
        that day, per cryo.telemetry.cost_attribution's rollup) and write it onto
        QueryCostAttributionDaily.estimated_scan_cost_usd -- previously always NULL, since
        there's no published per-byte Trino price the way there is for S3.

        Off by default (Settings.trino_cost_pricing_enabled) and requires CRYO_GRAFANA_TOKEN,
        same fail-safe-open contract as _maybe_check_cost_ceiling: silently no-ops rather than
        raising if either isn't set. Runs after _maybe_aggregate_cost_attribution in the tick
        order above so this tick's bytes-scanned denominator is as fresh as possible.
        """
        from datetime import timedelta

        from cryo.config import get_settings

        settings = get_settings()
        if not settings.trino_cost_pricing_enabled:
            return
        if self._last_trino_cost_pricing is not None:
            if current - self._last_trino_cost_pricing < timedelta(days=1):
                return

        import logging
        import os

        logger = logging.getLogger(__name__)
        token = os.environ.get("CRYO_GRAFANA_TOKEN")
        if not token:
            logger.debug("Trino scan-cost pricing skipped: CRYO_GRAFANA_TOKEN not set")
            return

        try:
            from cryo.cost.model import derive_trino_scan_cost_prices
            from cryo.reporting.cost_ceiling import fetch_namespace_cost_range
            from cryo.telemetry.cost_attribution import apply_scan_cost_prices, bytes_scanned_by_day

            since = (current - timedelta(days=settings.trino_cost_pricing_lookback_days)).date()

            day_costs = fetch_namespace_cost_range(
                grafana_url=settings.grafana_base_url,
                grafana_token=token,
                cluster_name=settings.cost_ceiling_cluster_name,
                namespace=settings.trino_cost_pricing_namespace,
                since=since,
            )
            namespace_cost_by_day = {dc.day: dc.total_cost_usd for dc in day_costs}

            with SessionLocal() as session:
                bytes_by_day = bytes_scanned_by_day(session, since=since)
                prices = derive_trino_scan_cost_prices(namespace_cost_by_day, bytes_by_day)
                updated = apply_scan_cost_prices(session, prices)
                session.commit()

            logger.info(
                "Trino scan-cost pricing tick: %s day(s) priced, %s rows updated",
                len(prices),
                updated,
            )
            self._last_trino_cost_pricing = current
        except Exception as exc:
            logger.warning("Trino scan-cost pricing tick failed: %s", exc)

    def _maybe_check_rollout_safety(self, current: datetime) -> None:
        from datetime import timedelta

        if self._last_rollout_safety_check is not None:
            if current - self._last_rollout_safety_check < timedelta(minutes=1):
                return

        try:
            from cryo.rollout.safety import run_rollout_safety_tick

            with SessionLocal() as session:
                result = run_rollout_safety_tick(session, now=current)

            import logging

            if result.halted or result.invalid:
                logging.getLogger(__name__).warning(
                    "Rollout safety tick: evaluated=%s halted=%s invalid=%s",
                    result.evaluated,
                    result.halted,
                    result.invalid,
                )
            self._last_rollout_safety_check = current
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("Rollout safety tick failed: %s", exc)

    def _maybe_check_unmatched_tables(self, current: datetime) -> None:
        """Periodically sweep for active tables that resolve to no effective preset.

        `effective_preset()` returning None (no pinned assignment, no matching rule) means
        the table is never scheduled for maintenance by any cron -- silently, forever, until
        someone manually assigns a preset. There's no platform-default fallback by design,
        so this isn't a bug in the matching logic itself, but until now nothing surfaced how
        many tables were actually falling into that gap.

        Real incident, 2026-08-12: found via manual investigation that 1,267 active tables
        with needs_compaction=True matched no enabled preset, including a 24TB table pinned
        to a preset that had since been disabled (a related, now-fixed bug in
        effective_preset's stale-pin handling -- see cryo/scheduler/rules.py). This sweep
        would have surfaced that gap as a metric instead of requiring ad-hoc debugging.
        """
        from datetime import timedelta

        from cryo.config import get_settings

        interval_hours = get_settings().unmatched_tables_check_interval_hours
        if self._last_unmatched_tables_check is not None:
            elapsed = current - self._last_unmatched_tables_check
            if elapsed < timedelta(hours=interval_hours):
                return

        try:
            import logging

            from cryo.observability.metrics import scheduler_unmatched_tables
            from cryo.scheduler.rules import effective_preset

            tables = self._store.list_tables()
            presets = self._store.list_presets()
            rules = self._store.list_rules()
            assignments = self._store.list_assignments()

            unmatched_needs_compaction: list[TableCandidate] = []
            unmatched_other = 0
            for table in tables:
                if effective_preset(table, presets, rules, assignments) is not None:
                    continue
                if bool(table.stats.get("needs_compaction")):
                    unmatched_needs_compaction.append(table)
                else:
                    unmatched_other += 1

            scheduler_unmatched_tables.labels(needs_compaction="true").set(
                len(unmatched_needs_compaction)
            )
            scheduler_unmatched_tables.labels(needs_compaction="false").set(unmatched_other)

            logger = logging.getLogger(__name__)
            if unmatched_needs_compaction:
                priority_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
                worst = sorted(
                    unmatched_needs_compaction,
                    key=lambda t: (
                        priority_rank.get(str(t.stats.get("compaction_priority", "LOW")), 3),
                        -float(t.stats.get("total_size_gb") or 0),
                    ),
                )[:10]
                logger.warning(
                    "Unmatched-tables sweep: %d active table(s) need compaction but match no "
                    "enabled preset (never scheduled). Largest/highest-priority: %s",
                    len(unmatched_needs_compaction),
                    ", ".join(
                        f"{t.schema_name}.{t.table_name} "
                        f"({t.stats.get('compaction_priority')}, "
                        f"{float(t.stats.get('total_size_gb') or 0):.0f}GB)"
                        for t in worst
                    ),
                )
            self._last_unmatched_tables_check = current
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("Unmatched-tables sweep failed: %s", exc)

    def _maybe_check_cost_ceiling(self, current: datetime) -> None:
        """Real-dollar circuit breaker: check the cryo namespace's actual daily spend
        (used+unused, via Grafana's Athena CUR datasource) and pause fleet-wide admission
        if it exceeds Settings.cost_ceiling_daily_usd. Off by default
        (cost_ceiling_enabled) -- see that field's docstring for why, and
        cryo.reporting.cost_ceiling's module docstring for the incident/request behind
        this.

        Requires CRYO_GRAFANA_TOKEN (see cryo-secrets) -- silently no-ops if it isn't
        set, same fail-safe-open contract as every other guardrail in this codebase that
        depends on an external credential.
        """
        from datetime import timedelta

        from cryo.config import get_settings

        settings = get_settings()
        if not settings.cost_ceiling_enabled:
            return
        if self._last_cost_ceiling_check is not None:
            elapsed = current - self._last_cost_ceiling_check
            if elapsed < timedelta(minutes=settings.cost_ceiling_check_interval_minutes):
                return

        import logging
        import os

        logger = logging.getLogger(__name__)
        token = os.environ.get("CRYO_GRAFANA_TOKEN")
        if not token:
            logger.debug("Cost ceiling check skipped: CRYO_GRAFANA_TOKEN not set")
            return

        try:
            from cryo.reporting.cost_ceiling import enforce_cost_ceiling

            with SessionLocal() as session:
                check = enforce_cost_ceiling(
                    session,
                    grafana_url=settings.grafana_base_url,
                    grafana_token=token,
                )
            if check is not None:
                # PDAE-8541: check.day is the most recent day with committed CUR rows, not
                # necessarily literally today (CUR lags 24-48h) -- log the real day so this
                # line can't be misread as "today's" spend when it's actually reporting
                # yesterday's (or older) fully-billed figure.
                logger.info(
                    "Cost ceiling check: cryo namespace $%.2f on %s (used $%.2f + unused "
                    "$%.2f) vs $%.2f ceiling%s",
                    check.total_cost_usd,
                    check.day.isoformat(),
                    check.used_cost_usd,
                    check.unused_cost_usd,
                    check.ceiling_usd,
                    " -- OVER BUDGET" if check.over_ceiling else "",
                )
            self._last_cost_ceiling_check = current
        except Exception as exc:
            logger.warning("Cost ceiling check failed: %s", exc)

    def _maybe_silent_data_loss_sweep(self, current: datetime) -> None:
        """Periodically run the silent-data-loss detector for a rotating slice of the fleet.

        Throttled by ``CRYO_SILENT_LOSS_CHECK_INTERVAL_MIN`` (default 5). Failures on one
        table are isolated -- the sweep must not abort just because one path is unreadable.
        Only sweeps ``CRYO_SILENT_LOSS_BATCH_SIZE`` tables per tick, resuming from
        ``self._silent_loss_cursor`` and wrapping back to the start of the fleet once it
        reaches the end -- see run_silent_data_loss_sweep's docstring for why (this used to
        load the entire fleet unbounded and OOMKilled cryo-scheduler every ~5 minutes).
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
                results, next_cursor = run_silent_data_loss_sweep(
                    session, after_id=self._silent_loss_cursor
                )
                session.commit()
            self._silent_loss_cursor = next_cursor
            if results:
                import logging

                logging.getLogger(__name__).info(
                    "silent_data_loss sweep: %d tables flagged",
                    len(results),
                )
            self._last_silent_loss_sweep = current
        except Exception as exc:  # pragma: no cover - defensive: don't break the scheduler.
            import logging

            logging.getLogger(__name__).warning("silent_data_loss sweep failed: %s", exc)

    def _sweep_pending_fallback_dispatches(self) -> None:
        """Resolve SUBMITTED audit rows (async Spark fallback dispatches, PDAE-8352) to
        their real terminal outcome.

        Runs every tick with no extra throttle -- like _dispatch_scheduled_runs above, the
        check itself is cheap and bounded (see sweep_pending_fallback_dispatches's own
        max_rows default), and a stuck-pending dispatch should get resolved close to when
        it actually finishes, not delayed by an internal interval.
        """
        import logging

        from cryo.governor.fallback_sweep import sweep_pending_fallback_dispatches

        logger = logging.getLogger(__name__)
        try:
            with SessionLocal() as session:
                result = sweep_pending_fallback_dispatches(session)
            if (
                result.resolved_ok
                or result.resolved_failed
                or result.resolved_unknown
                or result.lookup_errors
            ):
                logger.info(
                    "Fallback dispatch sweep: %d resolved ok, %d resolved failed, "
                    "%d resolved unknown (TTL-expired before resolution), "
                    "%d still pending, %d lookup errors",
                    result.resolved_ok,
                    result.resolved_failed,
                    result.resolved_unknown,
                    result.still_pending,
                    result.lookup_errors,
                )
        except Exception as exc:  # pragma: no cover - defensive: don't break the scheduler.
            logger.warning("Fallback dispatch sweep failed: %s", exc)

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
                    # fifo_priority (scheduler.py) keyed on the run's real created_at, not
                    # enqueue time -- see its own docstring for why every enqueue call site
                    # leaving priority at its 0 default made cross-run admission order
                    # effectively random by job UUID rather than fair or age-based.
                    priority = fifo_priority(run.created_at)
                    for job in jobs:
                        self._queue.enqueue(str(job.id), str(job.destination_id), priority)
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
    *,
    failure_history: Mapping[str, Mapping[str, int]] | None = None,
) -> ScheduledRunDraft:
    tables_by_id = {table.id: table for table in tables}
    routed = []
    for job in run.jobs:
        table = tables_by_id.get(job.table_id)
        destination_id = job.destination_id
        if table is not None:
            if preset.guardrails.get("adaptive_routing"):
                routing_table = table
                per_table_failures = (failure_history or {}).get(table.id)
                if per_table_failures:
                    # dataclasses.replace, not a mutation -- TableCandidate is frozen, and
                    # this per-run augmented copy must never leak into the shared `tables`
                    # list other jobs/presets in the same tick still read from.
                    routing_table = replace(
                        table,
                        stats={
                            **table.stats,
                            "engine_consecutive_failures": per_table_failures,
                        },
                    )
                route = resolve_destination_adaptive(
                    routing_table,
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
