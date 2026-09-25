"""Prometheus metrics for Cryo observability.

Exposes key metrics for:
- Governor admission decisions
- Worker job execution
- Scanner health
- Planner operation counts
- Queue depth and latency
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, Summary

# ==================== API SLO Metrics ====================

api_requests_total = Counter(
    "cryo_api_requests_total",
    "API requests by bounded route template, method, and status class.",
    ["route", "method", "status_class"],
)

api_request_duration_seconds = Histogram(
    "cryo_api_request_duration_seconds",
    "API request duration by bounded route template and method.",
    ["route", "method"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# ==================== Governor Metrics ====================

governor_admission_decisions = Counter(
    "cryo_governor_admission_decisions_total",
    "Total admission decisions by outcome",
    ["destination", "outcome"],  # outcome: admitted, deferred, blocked, orphaned
)

governor_wave_duration_seconds = Histogram(
    "cryo_governor_wave_duration_seconds",
    "Duration of governor admission waves",
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

governor_queue_depth = Gauge(
    "cryo_governor_queue_depth",
    "Number of jobs pending admission by destination",
    ["destination"],
)

governor_in_flight = Gauge(
    "cryo_governor_in_flight_jobs",
    "Number of jobs currently in flight by destination and replica",
    ["destination", "replica"],
)

governor_qpp_decisions_total = Counter(
    "cryo_governor_qpp_decisions_total",
    "QPP gate decisions during governor admission.",
    ["destination", "outcome"],  # outcome: confirmed, refused, error
)

# Real incident, 2026-09-15/16: NativeJobWatcher.tick() (reconciles K8s Job completion back to
# MaintenanceJob DB status) was wrapped in a bare `except Exception` at debug level in
# cli.py's _tick_native_watcher -- invisible in prod (logging is configured at INFO there).
# A run of transient K8s API failures (matching the same window's metastore/cluster
# congestion) could silently skip every tick with zero signal, leaving completed pods'
# MaintenanceJob rows stuck at status=running indefinitely (confirmed live: 164+ Completed
# native pods vs. 11 actually Running, but the DB still counted 258 as "running", which fed
# directly into native_admit.py's metastore-capacity headroom check as phantom load). This
# metric and the exception handling around it exist so a stretch of tick failures is visible
# (alertable) instead of a silent no-op.
native_watcher_tick_failures_total = Counter(
    "cryo_native_watcher_tick_failures_total",
    "Total NativeJobWatcher.tick() calls that raised instead of completing, by exception type.",
    ["exception_type"],
)

# Root-cause sweep, 2026-09-16: the native watcher's silent-debug-logging bug above was one
# instance of a repeated pattern in this codebase -- exception handlers around a genuine
# external-dependency probe (K8s API, Trino coordinator) written at logger.debug, which is
# invisible in prod (configure_logging sets INFO there; see cryo/observability/logging.py).
# cryo/engines/trino/query_status.py's get_query_state/_probe_one is the other real instance
# found in the same sweep: it fails safe (returns EngineOpState.UNCERTAIN, which
# orphan_recovery.py's quiet-window gates correctly hold on rather than misinterpreting as
# quiet), so it never caused a wrong finalize/orphan like the watcher bug did -- but a sustained
# run of Trino-connectivity failures still held safety gates indefinitely with zero operator
# visibility into why, which is its own real incident shape ("queue stuck, nobody knows").
trino_query_status_probe_failures_total = Counter(
    "cryo_trino_query_status_probe_failures_total",
    "Total Trino query-status probe calls (get_query_state/_probe_one) that raised instead of "
    "returning a real state, by probe name.",
    ["probe"],
)

# ==================== Worker Metrics ====================

worker_job_duration_seconds = Histogram(
    "cryo_worker_job_duration_seconds",
    "Duration of worker job execution",
    ["destination", "operation_type", "status"],
    buckets=[1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0, 3600.0],
)

worker_job_executions_total = Counter(
    "cryo_worker_job_executions_total",
    "Total worker job executions by status",
    ["destination", "operation_type", "status"],  # status: ok, error, timeout
)

worker_operation_bytes_processed = Counter(
    "cryo_worker_operation_bytes_processed_total",
    "Total bytes processed by maintenance operations",
    ["destination", "operation_type"],
)

worker_operation_files_processed = Counter(
    "cryo_worker_operation_files_processed_total",
    "Total files processed by maintenance operations",
    ["destination", "operation_type", "file_type"],  # file_type: input, output
)

# ==================== Scanner Metrics ====================

scanner_tables_scanned_total = Counter(
    "cryo_scanner_tables_scanned_total",
    "Total tables scanned by source",
    ["source"],
)

scanner_scan_duration_seconds = Histogram(
    "cryo_scanner_scan_duration_seconds",
    "Duration of individual table scans",
    ["source"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0],
)

scanner_health_scores = Summary(
    "cryo_scanner_health_scores",
    "Distribution of table health scores",
    ["source", "grade"],  # grade: A, B, C, D, E, F
)

scanner_small_files_detected = Counter(
    "cryo_scanner_small_files_detected_total",
    "Total small files detected across all tables",
    ["source"],
)

scanner_errors_total = Counter(
    "cryo_scanner_errors_total",
    "Total scanner errors by source",
    ["source", "error_type"],
)

# ==================== Planner Metrics ====================

planner_plans_generated_total = Counter(
    "cryo_planner_plans_generated_total",
    "Total maintenance plans generated by mode",
    ["mode"],  # mode: standard, deep, light, manifest_only
)

planner_operations_planned_total = Counter(
    "cryo_planner_operations_planned_total",
    "Total operations planned by type",
    ["operation_type"],
)

planner_partitions_selected = Histogram(
    "cryo_planner_partitions_selected",
    "Number of partitions selected for optimize operations",
    buckets=[1, 5, 10, 20, 50, 100, 200, 500, 1000],
)

planner_benefit_scores = Summary(
    "cryo_planner_benefit_scores",
    "Distribution of AutoComp benefit scores for selected partitions",
)

planner_skipped_reasons_total = Counter(
    "cryo_planner_skipped_reasons_total",
    "Total tables skipped by reason",
    ["reason"],  # reason: below_threshold, no_partitions, recently_maintained
)

# ==================== Queue Metrics ====================

queue_pending_jobs = Gauge(
    "cryo_queue_pending_jobs",
    "Number of jobs pending in queue by destination",
    ["destination"],
)

queue_enqueue_duration_seconds = Histogram(
    "cryo_queue_enqueue_duration_seconds",
    "Duration of queue enqueue operations",
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0],
)

queue_dequeue_duration_seconds = Histogram(
    "cryo_queue_dequeue_duration_seconds",
    "Duration of queue dequeue operations",
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0],
)

# ==================== Scheduler Metrics ====================

scheduler_trigger_evaluations_total = Counter(
    "cryo_scheduler_trigger_evaluations_total",
    "Total trigger evaluations by outcome",
    ["trigger_type", "outcome"],  # outcome: triggered, skipped, error
)

scheduler_signal_detections_total = Counter(
    "cryo_scheduler_signal_detections_total",
    "Total maintenance signals detected by type and severity",
    ["signal_type", "severity"],  # signal_type: file_degradation, latency_spike, cost_spike
)

scheduler_approval_requests_total = Counter(
    "cryo_scheduler_approval_requests_total",
    "Total approval requests by operation type and status",
    ["operation_type", "status"],  # status: pending, approved, rejected, expired
)

scheduler_unmatched_tables = Gauge(
    "cryo_scheduler_unmatched_tables",
    "Active tables that resolve to no effective preset (no pinned assignment, no matching "
    "rule) and so are never scheduled for maintenance by any cron. Split by whether the "
    "table's latest scan flagged needs_compaction=True, since that's the actionable subset.",
    ["needs_compaction"],
)

rollout_decisions_total = Counter(
    "cryo_rollout_decisions_total",
    "Cohort rollout safety decisions by declared phase.",
    ["phase", "decision"],
)

audit_retention_deleted_total = Counter(
    "cryo_audit_retention_deleted_total",
    "Maintenance audit rows deleted by the configured retention policy.",
)

audit_retention_backlog = Gauge(
    "cryo_audit_retention_backlog",
    "Maintenance audit rows older than the configured retention cutoff after the last sweep.",
)

# ==================== Cost Metrics ====================

cost_storage_bytes_total = Gauge(
    "cryo_cost_storage_bytes_total",
    "Total storage bytes across all tables by source",
    ["source"],
)

cost_monthly_estimate_usd = Gauge(
    "cryo_cost_monthly_estimate_usd",
    "Estimated monthly cost by source and cost type",
    ["source", "cost_type"],  # cost_type: storage, compute, maintenance
)

cost_savings_projected_usd = Gauge(
    "cryo_cost_savings_projected_usd",
    "Projected monthly savings from maintenance by source",
    ["source"],
)

# Fleet gauges are republished from the same SQL aggregates on every API replica.
# Aggregate with max(), never sum().
_FLEET = "Aggregate with max() across API replicas; each replica publishes the same SQL aggregate."

datalake_grade_tables = Gauge(
    "cryo_datalake_grade_tables",
    f"Active tables by health grade. {_FLEET}",
    ["grade"],
)

datalake_small_files = Gauge(
    "cryo_datalake_small_files",
    f"Small files across active tables. delta() is the drift signal. {_FLEET}",
)

datalake_avg_health_score = Gauge(
    "cryo_datalake_avg_health_score",
    f"Mean health score of active tables with stats. {_FLEET}",
)

datalake_total_size_gb = Gauge(
    "cryo_datalake_total_size_gb",
    f"Sum of active-table size in GB. delta() is size drift. {_FLEET}",
)

scan_coverage_ratio = Gauge(
    "cryo_scan_coverage_ratio",
    f"Active tables scanned within the window, divided by active tables. {_FLEET}",
    ["window"],
)

projected_savings_usd = Gauge(
    "cryo_projected_savings_usd",
    f"Projected monthly USD savings by operation, from reclaim bytes and price. {_FLEET}",
    ["operation"],
)

reclaimable_bytes = Gauge(
    "cryo_reclaimable_bytes",
    f"Reclaimable bytes by operation, plus operation=total from table stats. {_FLEET}",
    ["operation"],
)

realized_bytes_freed = Gauge(
    "cryo_realized_bytes_freed",
    f"Measured bytes freed by successful reclaim actions in the window. {_FLEET}",
    ["window"],
)

realized_vs_projected_ratio = Gauge(
    "cryo_realized_vs_projected_ratio",
    f"30d realized bytes divided by current projected reclaim bytes. 0 if projected is 0. {_FLEET}",
)

orphan_bytes_total = Gauge(
    "cryo_orphan_bytes",
    f"Sum of orphan_bytes on active tables. {_FLEET}",
)

orphan_scan_oldest_age_seconds = Gauge(
    "cryo_orphan_scan_oldest_age_seconds",
    f"Age of the oldest full cost scan among active tables. 0 if none. {_FLEET}",
)

orphan_scan_coverage_ratio = Gauge(
    "cryo_orphan_scan_coverage_ratio",
    f"Active tables with a full cost scan, divided by active tables. {_FLEET}",
)

jobs_live = Gauge(
    "cryo_jobs_live",
    f"Non-terminal maintenance jobs by status. SQL count, not a row load. {_FLEET}",
    ["status"],
)

# ==================== Anomaly Detection Metrics ====================

anomaly_alerts_total = Counter(
    "cryo_anomaly_alerts_total",
    "Total anomaly alerts by metric and severity",
    ["metric_name", "severity"],
)

anomaly_detection_duration_seconds = Histogram(
    "cryo_anomaly_detection_duration_seconds",
    "Duration of anomaly detection runs",
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0],
)

# ==================== LLM / AI Metrics ====================

llm_tokens_total = Counter(
    "cryo_llm_tokens_total",
    "Total LLM tokens consumed by provider and feature.",
    ["provider", "feature"],
)

llm_budget_denials_total = Counter(
    "cryo_llm_budget_denials_total",
    "Total LLM calls denied by budget guardrails.",
    ["provider", "reason"],
)

# ==================== Durable Safety Metrics ====================

maintenance_intent_transitions_total = Counter(
    "cryo_maintenance_intent_transitions_total",
    "Durable maintenance intent state transitions.",
    ["from_state", "to_state"],
)

maintenance_attempts_started_total = Counter(
    "cryo_maintenance_attempts_started_total",
    "Durable maintenance attempts started.",
)

maintenance_attempt_results_total = Counter(
    "cryo_maintenance_attempt_results_total",
    "Durable maintenance attempts by terminal outcome.",
    ["status"],
)

maintenance_effect_transitions_total = Counter(
    "cryo_maintenance_effect_transitions_total",
    "Durable effect receipt transitions by operation and strategy.",
    ["operation", "strategy", "from_state", "to_state"],
)

maintenance_pause_changes_total = Counter(
    "cryo_maintenance_pause_changes_total",
    "Maintenance pause and resume control changes.",
    ["scope_type", "action"],
)

maintenance_pause_blocks_total = Counter(
    "cryo_maintenance_pause_blocks_total",
    "Effects blocked after observing an active maintenance pause.",
    ["scope_type"],
)

maintenance_pause_propagation_seconds = Histogram(
    "cryo_maintenance_pause_propagation_seconds",
    "Time from a pause control update until an effect path observes it.",
    ["scope_type"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

maintenance_reconciliation_duration_seconds = Histogram(
    "cryo_maintenance_reconciliation_duration_seconds",
    "Duration of one uncertain-effect reconciliation pass.",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

maintenance_reconciliation_outcomes_total = Counter(
    "cryo_maintenance_reconciliation_outcomes_total",
    "Uncertain-effect reconciliation outcomes.",
    ["outcome"],
)

maintenance_reconciliation_backlog = Gauge(
    "cryo_maintenance_reconciliation_backlog",
    "Effects currently due for reconciliation.",
)

maintenance_reconciliation_oldest_age_seconds = Gauge(
    "cryo_maintenance_reconciliation_oldest_age_seconds",
    "Age of the oldest effect currently due for reconciliation.",
)
