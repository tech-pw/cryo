from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven platform infrastructure settings."""

    model_config = SettingsConfigDict(
        env_prefix="CRYO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Cryo"
    environment: Literal["dev", "test", "staging", "prod"] = "dev"
    database_url: str = "postgresql+psycopg://cryo:cryo@localhost:5432/cryo"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "dev-only-change-me"
    jwt_secret: str | None = Field(
        default=None,
        description="HS256 secret for API JWTs. Defaults to secret_key when unset.",
    )
    enforce_tenant_isolation: bool = Field(
        default=False,
        description="When true, every JWT must carry tenant_id and queries are scoped.",
    )
    oidc_issuer_url: str | None = Field(
        default=None,
        description="OIDC issuer URL (production SSO). Enables RS256 IdP token verification.",
    )
    oidc_client_id: str | None = Field(default=None, description="OIDC OAuth client id.")
    oidc_client_secret: str | None = Field(
        default=None,
        description="OIDC client secret for authorization-code exchange.",
    )
    oidc_audience: str | None = Field(
        default=None,
        description="Expected access-token audience; defaults to oidc_client_id.",
    )
    oidc_jwks_uri: str | None = Field(
        default=None,
        description="Optional JWKS URI override (defaults to issuer/.well-known/jwks.json).",
    )
    oidc_redirect_uri: str | None = Field(
        default=None,
        description="OAuth redirect URI registered with the IdP (UI callback).",
    )
    allow_dev_token_in_prod: bool = Field(
        default=False,
        description=(
            "Explicit opt-in to keep POST /api/v1/auth/token (self-declared role, no real "
            "identity check) enabled even when environment=='prod'. Only for bridging the gap "
            "before OIDC is configured -- anyone who can reach the API can self-grant any role "
            "while this is true. Turn off once oidc_issuer_url is set."
        ),
    )
    cors_allow_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    scan_num_chunks: int = Field(default=4, gt=0)
    scan_small_file_mb: float = Field(default=89.6, gt=0)
    scan_needs_compaction_threshold: int = Field(default=128, gt=0)

    # Fleet-cost day-over-day / week-over-week spike & decline thresholds for the daily
    # report (see cryo/reporting/cost_signals.py). Defaults are reasonable starting points,
    # not tuned to any specific deployment -- override per-environment as real threshold
    # noise is observed. WoW bands are wider than DoD's: a week-long window naturally carries
    # more workload-drift noise, but catches slow creep DoD's tighter window misses.
    report_cost_dod_spike_pct: float = Field(
        default=10.0, gt=0, description="Day-over-day fleet cost increase (%) that flags a spike."
    )
    report_cost_dod_decline_pct: float = Field(
        default=30.0,
        gt=0,
        description="Day-over-day fleet cost decrease (%) that flags a decline worth noting.",
    )
    report_cost_wow_spike_pct: float = Field(
        default=15.0, gt=0, description="Week-over-week fleet cost increase (%) that flags a spike."
    )
    report_cost_wow_decline_pct: float = Field(
        default=35.0,
        gt=0,
        description="Week-over-week fleet cost decrease (%) that flags a decline worth noting.",
    )
    # Trino QUERY/compute cost day-over-day / week-over-week thresholds (see
    # cryo/reporting/query_cost_signals.py) -- deliberately separate from the report_cost_*
    # fleet-STORAGE thresholds above. Query cost is driven by workload (who ran what, when),
    # not table state, so it has its own noise profile and its own thresholds rather than
    # reusing the storage-cost ones.
    report_query_cost_dod_spike_pct: float = Field(
        default=15.0,
        gt=0,
        description="Day-over-day Trino query cost increase (%) that flags a spike.",
    )
    report_query_cost_dod_decline_pct: float = Field(
        default=30.0,
        gt=0,
        description="Day-over-day Trino query cost decrease (%) that flags a decline worth noting.",
    )
    report_query_cost_wow_spike_pct: float = Field(
        default=20.0,
        gt=0,
        description="Week-over-week Trino query cost increase (%) that flags a spike.",
    )
    report_query_cost_wow_decline_pct: float = Field(
        default=35.0,
        gt=0,
        description="Week-over-week Trino query cost decrease (%) that flags a decline.",
    )
    # North-star maintenance-impact metric (PDAE, added 2026-08-19): unlike the cost thresholds
    # above (less cost = good), more GB reclaimed = good -- direction is the sign of the pct
    # change, not a spike/decline split. Wider bands than cost's for the same reason WoW is
    # wider than DoD there: daily reclaim totals are lumpier than daily cost (a handful of large
    # tables finishing on one day vs. spread across a week swings the total a lot without
    # meaning anything changed about whether Cryo is working).
    report_impact_dod_move_pct: float = Field(
        default=25.0,
        gt=0,
        description="Day-over-day change (%) in GB reclaimed that's worth flagging either way.",
    )
    report_impact_wow_move_pct: float = Field(
        default=20.0,
        gt=0,
        description="Week-over-week change (%) in GB reclaimed that's worth flagging either way.",
    )
    # Off by default: these three posted to the same channel as the daily report and each
    # repeated its full body every scheduler restart. The daily report is the one deliberate
    # digest; turn these back on individually if a specific signal is wanted again.
    shared_s3_report_enabled: bool = Field(
        default=False,
        description="Post the shared-S3-path anomaly report to Slack.",
    )
    query_cost_report_enabled: bool = Field(
        default=False,
        description="Post the Trino query-cost report to Slack.",
    )
    production_health_check_enabled: bool = Field(
        default=False,
        description="Post the production health check to Slack.",
    )
    # Batch maintenance (cryo.maintenance.run): one CronJob selects the top tables, runs them
    # across a few local-mode Spark pods, rescans them with the Go scanner and posts one
    # Slack message. See docs/BATCH_MAINTENANCE.md.
    maintenance_top_n: int = Field(default=500, ge=1, description="Tables per batch run.")
    maintenance_destination_id: str | None = Field(
        default=None,
        description="Spark destination to run on; defaults to the only enabled Spark one.",
    )
    maintenance_max_lanes: int = Field(default=4, ge=1, le=16, description="Max Spark pods.")
    maintenance_lane_max_cpu_milli: int = Field(default=4000, ge=500)
    maintenance_lane_max_mem_mib: int = Field(default=16384, ge=2048)
    maintenance_lane_target_gb: float = Field(
        default=200.0,
        gt=0,
        description="Rewrite GB one lane should carry before another pod is worth starting.",
    )
    maintenance_max_lane_respawns: int = Field(
        default=8,
        ge=0,
        description=(
            "Replacement pods one run may start for lanes that died mid-run (OOM, node loss). "
            "The table a lane died on is recorded as failed; the replacement takes the rest."
        ),
    )
    maintenance_lane_capacity_types: str = Field(
        default="spot,on-demand",
        description=(
            "Karpenter capacity types lane pods may run on, most preferred first. Replaces the "
            "karpenter.sh/capacity-type pin lanes inherit from the Spark destination's node "
            "selector; empty keeps that pin. Lanes tolerate node loss (partial-progress commits, "
            "replacement lanes), so they don't need the on-demand-only rule the per-statement "
            "Spark path has."
        ),
    )
    maintenance_max_runtime_minutes: int = Field(
        default=360,
        ge=15,
        description="Hard stop for the whole run; lanes stop starting tables 20 min earlier.",
    )
    maintenance_cooldown_hours: int = Field(default=20, ge=0)
    maintenance_max_stats_age_days: int = Field(
        default=3,
        ge=1,
        description="Skip tables whose last successful scan is older than this.",
    )
    maintenance_shared_snapshot_max_age_hours: int = Field(
        default=72,
        ge=1,
        description=(
            "If the shared-S3-path snapshot cannot be refreshed and the last one is older "
            "than this, the run skips remove_orphan_files on every table."
        ),
    )
    # AutoComp ranking (cryo/maintenance/selection.py). Weights must sum to 1, so the compute
    # cost weight is 1 - maintenance_autocomp_w_file_reduction; 0.7 is the paper's evaluation.
    maintenance_autocomp_w_file_reduction: float = Field(default=0.7, ge=0.0, le=1.0)
    maintenance_autocomp_heat_weight: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Cryo extension: share of the score from Trino query heat; 0 = paper-only.",
    )
    maintenance_autocomp_rewrite_gb_per_hour: float = Field(
        default=25.0,
        gt=0,
        description=(
            "RewriteBytesPerHour until a run has measured it; afterwards the median of recent "
            "runs' observed rewrite throughput is used."
        ),
    )
    maintenance_duration_model: Literal["rate", "ernest"] = Field(
        default="rate",
        description=(
            "How AutoComp estimates a table's rewrite hours. 'rate': GiB / RewriteBytesPerHour. "
            "'ernest': the Ernest model fitted on completed rewrite statements "
            "(cryo/maintenance/ernest.py), used only while it beats 'rate' on held-out runs; "
            "the comparison is recorded in every run's autocomp trace either way."
        ),
    )
    maintenance_autocomp_budget_gbhr: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Compute budget per scheduled run. Unset: max_lanes x lane memory x the hours "
            "lanes may start tables."
        ),
    )
    maintenance_poll_seconds: int = Field(default=30, ge=5)
    maintenance_rescan_enabled: bool = Field(default=True)
    maintenance_slack_enabled: bool = Field(default=True)
    scan_freshness_excluded_schemas: str = Field(
        default="temp",
        description=(
            "Comma-separated schema names excluded from scan_freshness/scan_failures (health "
            "checks) -- for scratch/staging schemas whose tables are never queried, so a slow "
            "scan cadence against them isn't a real signal. Confirmed live 2026-09-28: schema "
            "'temp' (341 active tables) had zero query_logs rows ever, on any table, but "
            "contributed 166 of 209 scan_freshness stale-table flags purely from losing out on "
            "scan priority to tables that are actually read -- a real capacity tradeoff, not a "
            "scanner problem worth alerting on. Empty string excludes nothing."
        ),
    )
    scan_dead_table_failure_threshold: int = Field(
        default=10,
        ge=1,
        description=(
            "consecutive_scan_failures at or above this AND zero query_logs rows in "
            "scan_dead_table_lookback_days -> auto-deactivate the table. Both conditions must "
            "hold: a table with any recent query traffic is never deactivated regardless of "
            "scan failure streak. Confirmed live 2026-09-28: 148 tables past this threshold "
            "(up to 175 consecutive failures) had zero Trino query traffic in 30 days -- "
            "stale catalog entries (deleted __dbt_tmp/backfill metadata), not live tables "
            "Cryo was failing to read."
        ),
    )
    scan_dead_table_lookback_days: int = Field(default=30, ge=1)
    dispatch_heartbeat_stall_minutes: int = Field(
        default=1560,
        ge=1,
        description=(
            "Minutes since the last MaintenanceJob admitted/started/finished before "
            "vitals.dispatch_heartbeat calls dispatch stalled. Cryo's only dispatch source is "
            "the once-daily maintenance CronJob (default 26h gap, schedule permitting), not a "
            "continuously-draining queue -- this must exceed that gap plus the run's own "
            "max_runtime_minutes, or every quiet hour between runs reads as an outage."
        ),
    )
    scan_parallel_tables: int = Field(
        default=0,
        ge=0,
        description="Tables to scan in parallel per chunk (0=serial).",
    )
    scan_parallel_manifests: int = Field(
        default=4,
        ge=1,
        description=(
            "Max threads for parallel manifest fetching within a table. "
            "Kept modest under typical 2Gi scanner-worker limits: each in-flight "
            "manifest still materializes its entry list."
        ),
    )
    # Samples cover only the (at most 3) numeric/temporal columns the query log shows being
    # range-filtered, so no text values are stored; they live on the current table_stats row
    # only, never in table_stats_history.
    scanner_wair_file_sample_size: int = Field(
        default=1000,
        ge=0,
        le=10_000,
        description=(
            "Maximum deterministic current-snapshot data-file bound samples retained per table "
            "for WAIR analysis, restricted to range-queried numeric/temporal columns. Zero "
            "disables collection."
        ),
    )
    scan_engine: Literal["pyiceberg", "go"] = Field(
        default="pyiceberg",
        description=(
            "Metadata walk implementation for current-snapshot stats. "
            "'go' shells out to cryo-engine-go scan-stats (streaming manifests); "
            "'pyiceberg' uses the in-process path."
        ),
    )
    scan_go_binary: str | None = Field(
        default=None,
        description="Optional path to cryo-engine-go; otherwise resolve like native runner.",
    )
    cost_metadata_max_files_estimate: int = Field(
        default=2_000_000,
        gt=0,
        description=(
            "Cheap pre-filter before even attempting Tier-1 metadata cost accounting: skip "
            "it when the current-snapshot file count meets or exceeds this threshold. The "
            "real memory guard is inside account_metadata_cost itself (cost_scan_max_snapshots"
            "/cost_scan_max_manifests, checked live during the walk, with a graceful heuristic "
            "fallback either way) -- this is just an outer bound for pathological cases, not "
            "the primary defense. Confirmed live, 2026-08-03: the previous default (200_000) "
            "excluded a table with 207,511 files (24TB, ~$700/mo, the single most cost-"
            "relevant table in the fleet) from all cost/reclaim accounting -- table_cost had "
            "zero rows for it, ever -- for a file count barely above the threshold and nowhere "
            "near what the internal walk can actually handle."
        ),
    )
    cost_metadata_max_manifests_current: int = Field(
        default=500,
        gt=0,
        description=(
            "Skip Tier-1 metadata cost when current-snapshot manifest count meets "
            "or exceeds this threshold."
        ),
    )

    cost_price_per_gb_month: float = Field(default=0.023, gt=0)
    cost_use_gib: bool = True
    cost_scan_tier: Literal["heuristic", "metadata", "storage_truth"] = "metadata"
    cost_scan_max_snapshots: int = Field(default=200, gt=0)
    cost_scan_max_manifests: int = Field(default=5000, gt=0)
    cost_full_scan_every_hours: int = Field(default=168, ge=0)
    cost_orphan_min_age_hours: int = Field(default=72, ge=0)
    cost_scan_max_list_keys: int = Field(default=1_000_000, gt=0)
    default_retention_days: int = Field(default=7, gt=0)
    default_retain_last: int = Field(default=1, gt=0)
    s3_get_usd_per_1k: float = Field(default=0.0004, ge=0)
    s3_list_usd_per_1k: float = Field(default=0.005, ge=0)
    # PUT/COPY/POST share AWS's "Tier1" request pricing with LIST (same $/1k as
    # s3_list_usd_per_1k above, as of this writing) -- kept as a distinct setting rather than
    # reusing s3_list_usd_per_1k so the two can diverge if AWS ever re-prices them separately,
    # and so fetch_s3_request_prices() has an explicit, named fallback default to pass rather
    # than callers reaching for a same-value-but-differently-named constant. This is the
    # fallback only -- see docs/s3-request-cost-tracking.md §6.4 for the live-fetched path.
    s3_put_usd_per_1k: float = Field(default=0.005, ge=0)
    query_gets_per_file: float = Field(default=1.0, ge=0)

    # Equal start/end disables the off-peak gate (heavy modes may run anytime).
    offpeak_start_ist: int = Field(default=0, ge=0, le=23)
    offpeak_end_ist: int = Field(default=0, ge=0, le=23)
    default_per_replica_optimize_cap: int = Field(default=5, gt=0)
    default_emr_slot_cap: int = Field(default=10, gt=0)
    default_native_slot_cap: int = Field(default=5, gt=0)
    wave_wait_sec: int = Field(default=300, gt=0)
    max_jobs_per_scheduled_run: int = Field(
        default=500,
        gt=0,
        description=(
            "Hard cap on how many tables one cron-triggered run may target. build_scheduled_run "
            "has always accepted a budget_hint, but the cron path never passed one, so a single "
            "preset fired against every matching table in the fleet. Live 2026-09-21: scheduled "
            "runs of 3,478-6,117 jobs against a worker fleet whose real ceiling is ~3,000 "
            "jobs/day, so 5,952 of 6,117 jobs in one run aged out of ADMITTED and were recorded "
            "as failures without an engine ever touching the table. Candidates are ranked by "
            "apply_priority_order before this slice, so the cap keeps the highest-value tables."
        ),
    )
    stuck_admitted_after_seconds: int = Field(
        default=900,
        gt=0,
        description=(
            "Grace period before reconcile_stuck_admitted_jobs treats an ADMITTED job with no "
            "engine_ref as undispatched. Only applies to jobs that are genuinely absent from "
            "the claim queue; a job still waiting its turn is left alone however long the "
            "queue is."
        ),
    )
    worker_dispatch_backlog_per_replica: int = Field(
        default=3,
        ge=1,
        description=(
            "Admission backpressure: the governor will not grow the undispatched ADMITTED "
            "backlog beyond this many jobs per live worker replica. Engine slot_cap bounds how "
            "much load the ENGINE can take; it says nothing about how fast workers can drain "
            "the claim queue, and workers process one job at a time. Without this the governor "
            "admits against engine capacity alone and buries the claim queue under work no "
            "worker can reach inside the stuck-admitted grace period."
        ),
    )
    engine_connect_timeout_sec: float = Field(
        default=5.0,
        gt=0,
        le=60.0,
        description=(
            "Per-request connect timeout (seconds) for Trino coordinator probes "
            "and statement execution. The trino client doesn't expose a connect "
            "timeout directly, so we patch the requests Session it uses."
        ),
    )
    scan_table_timeout_seconds: float = Field(
        default=180.0,
        gt=0,
        le=1800.0,
        description=(
            "Hard wall-clock bound for scanning a single table (catalog load, stats "
            "analysis, and orphan-file listing). Catalog/S3 clients don't reliably honor "
            "their own timeouts against a slow or unresponsive Hive Metastore/S3 endpoint, "
            "so this work runs in a bounded worker and is treated as a failed scan past "
            "this limit instead of blocking the scanner daemon's single poll loop forever."
        ),
    )
    scanner_max_partitions_per_table: int = Field(
        default=5000,
        gt=0,
        description=(
            "Cap on how many partition rows a single scan persists to table_partitions per "
            "table, keeping the ones with the most small files (the actual compaction signal). "
            "Real catalogs have a long tail of tables partitioned far more finely than any "
            "compaction decision needs -- one observed table had 397k partitions (median "
            "table: 8) and alone accounted for ~10% of table_partitions' total row count."
        ),
    )
    native_engine_enabled: bool = Field(
        default=True,
        description="Enable native cryo-engine maintenance (default workflow).",
    )
    flink_engine_enabled: bool = Field(
        default=False,
        description="Enable Flink config-generation engine (does not submit jobs).",
    )
    hilbert_clustering_enabled: bool = Field(
        default=False,
        description=(
            "Allow Hilbert-curve clustering. Off by default: Iceberg only gained hilbert() "
            "in 1.12.0 on the Spark 4.1 path (PR #16827, merged 2026-08-25), and an "
            "unsupported runtime fails at procedure-parse time after the job is submitted. "
            "Requests downgrade to Z-order unless the versions below confirm support."
        ),
    )
    spark_iceberg_version: str | None = Field(
        default=None,
        description=(
            "Iceberg runtime version on the Spark destination, e.g. '1.12.0'. Used to gate "
            "Hilbert clustering; unknown is treated as unsupported, never as new enough."
        ),
    )
    spark_runtime_version: str | None = Field(
        default=None,
        description=(
            "Spark version on the destination, e.g. '4.1.0'. Used to gate Hilbert clustering; "
            "unknown is treated as unsupported."
        ),
    )
    health_port: int = Field(
        default=8081,
        ge=1024,
        le=65535,
        description="HTTP port for /healthz and /metrics on background services.",
    )
    telemetry_ingest_on_startup: bool = Field(
        default=True,
        description="When true, scheduler attempts query-log ingest ticks if configured.",
    )
    telemetry_trino_url: str | None = Field(
        default=None,
        description=(
            "Trino coordinator URL for scheduled query-log ingestion (CRYO_TELEMETRY_TRINO_URL)."
        ),
    )
    telemetry_trino_user: str | None = Field(
        default=None,
        description=(
            "Username for telemetry_trino_url, if the coordinator requires auth "
            "(CRYO_TELEMETRY_TRINO_USER). Defaults to 'cryo' if a password is set but no "
            "username is given."
        ),
    )
    telemetry_trino_password: str | None = Field(
        default=None,
        description=(
            "Password for telemetry_trino_url, if the coordinator requires auth "
            "(CRYO_TELEMETRY_TRINO_PASSWORD). Unset means no BasicAuthentication is attempted."
        ),
    )
    telemetry_trino_ssl_verify: bool = Field(
        default=True,
        description=(
            "Verify TLS certificates when connecting to telemetry_trino_url "
            "(CRYO_TELEMETRY_TRINO_SSL_VERIFY). Only disable for coordinators with "
            "self-signed/internal-CA certs you've already verified out of band."
        ),
    )
    telemetry_athena_workgroup: str | None = Field(
        default=None,
        description="Optional Athena workgroup filter for query-log ingestion.",
    )
    s3_log_trino_catalog: str = Field(
        default="hive",
        description=(
            "Trino Hive-connector catalog/schema used only to clean up an external log table "
            "from an older Trino-based ingestion run when tearing down S3 request-cost "
            "tracking for a bucket (cryo/integrations/s3_bucket_provisioning.py). Ingestion "
            "itself reads and aggregates S3 access logs natively -- no Trino dependency."
        ),
    )
    s3_log_trino_schema: str = Field(
        default="cryo_s3_logs",
        description="Schema within s3_log_trino_catalog for the legacy teardown path above.",
    )
    query_log_retention_days: int = Field(
        default=90,
        gt=0,
        description=(
            "Days of raw query_logs rows to keep before the scheduler purges them. "
            "query_patterns (the aggregated signal partition/layout recommendations actually "
            "consume) is unaffected by purging, since it's aggregated before rows age out. "
            "Default (90d) is set at or above every consumer's own lookback window "
            "(the longest being cryo/tiering/s3_analyzer.py's 90-day default) so purging never "
            "silently starves a feature of data it still needs."
        ),
    )
    audit_log_retention_days: int = Field(
        default=365,
        ge=90,
        le=3650,
        description=(
            "Days to retain immutable maintenance audit entries in the operational database. "
            "The leader scheduler prunes older rows in bounded batches. Set a longer value when "
            "regulatory or incident-response policy requires it; 365 days is the safe default."
        ),
    )
    audit_log_prune_batch_size: int = Field(
        default=10_000,
        ge=100,
        le=100_000,
        description="Maximum audit rows deleted in one transaction.",
    )
    audit_log_prune_max_rows_per_tick: int = Field(
        default=100_000,
        ge=100,
        le=1_000_000,
        description="Maximum audit rows deleted by one daily leader-scheduler retention tick.",
    )
    query_log_iceberg_window_hours: int = Field(
        default=1,
        gt=0,
        description=(
            "Time-window size for the iceberg_table read-through query-log path "
            "(cryo/telemetry/iceberg_source.py). Each window is scanned and watermarked "
            "independently so a busy Trino cluster's query-event-listener table (which can log "
            "millions of rows over the default 7-day first-run lookback, and whose raw SQL text "
            "can be large per row) never has to be materialized into memory as one unbounded scan. "
            "Default is deliberately small (1h): measured against a real busy cluster, a single "
            "call processing 8 x 6h windows peaked at ~2.3GB, which exceeds the scheduler "
            "deployment's default 2Gi memory limit (the process that runs this automatically) "
            "even though it stayed under the API pod's 3Gi. 1h keeps per-call peak memory well "
            "under either limit; tune upward only if profiled safe for your own query volume."
        ),
    )
    query_log_iceberg_max_windows_per_tick: int = Field(
        default=4,
        gt=0,
        description=(
            "Max number of query_log_iceberg_window_hours windows processed per aggregation "
            "call. Bounds how much a large backlog (first-ever run, or catch-up after an outage) "
            "can do in one tick; the rest is picked up on the next scheduled tick."
        ),
    )
    query_log_iceberg_max_rows_per_window: int = Field(
        default=25_000,
        gt=0,
        description=(
            "Last-resort per-window row cap for the iceberg_table read-through query-log path. "
            "Only triggers if a single window still has an extreme row count; excess rows in "
            "that window are dropped (logged as a warning) rather than blocking the watermark "
            "from advancing past that window."
        ),
    )
    telemetry_duckdb_enabled: bool = Field(
        default=False,
        description="Enable DuckDB query-log ingestion (dev-only).",
    )
    telemetry_duckdb_database: str | None = Field(
        default=None,
        description="DuckDB database path for query_history / duckdb_logs ingestion.",
    )
    gitops_repo_path: str | None = Field(
        default=None,
        description="Local path to GitOps policy repository for webhook/poll sync.",
    )
    gitops_policy_dir: str = Field(
        default="policies",
        description="Relative directory within gitops_repo_path containing YAML policies.",
    )
    gitops_webhook_secret: str | None = Field(
        default=None,
        description="HMAC secret for GitOps webhook signature verification.",
    )
    otel_enabled: bool = Field(
        default=False,
        description="Enable OpenTelemetry tracing and OTLP export.",
    )
    otel_endpoint: str = Field(
        default="http://localhost:4318/v1/traces",
        description="OTLP HTTP endpoint for trace export.",
    )
    otel_service_name: str = Field(
        default="cryo",
        description="Service name attached to exported spans.",
    )
    otel_sample_rate: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Trace sampling rate (0.0-1.0).",
    )
    script_runtime: Literal["docker", "runsc"] = Field(
        default="docker",
        description="Container runtime for custom maintenance scripts (runsc=gVisor).",
    )
    credential_vending_enabled: bool = Field(
        default=False,
        description="Enable REST catalog STS credential vending in worker.",
    )
    smtp_host: str | None = Field(default=None, description="SMTP host for email notifications.")
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_user: str | None = Field(default=None)
    smtp_password: str | None = Field(default=None)
    smtp_from: str | None = Field(default=None, description="From address for report emails.")
    smtp_use_tls: bool = Field(default=True)
    report_email_recipients: list[str] = Field(
        default_factory=list,
        description="Email recipients for daily anomaly reports.",
    )
    ml_drift_check_interval_hours: int = Field(
        default=24,
        ge=1,
        description="Hours between scheduled health-classifier drift evaluations.",
    )
    unmatched_tables_check_interval_hours: int = Field(
        default=6,
        ge=1,
        description=(
            "Hours between fleet-wide sweeps for active tables that resolve to no effective "
            "preset (cryo.scheduler.rules.effective_preset returns None) -- these are never "
            "scheduled for maintenance by any cron and previously went undetected. Matches "
            "the scanner cadence (0 */6 * * *) since that's when needs_compaction can change. "
            "Real incident, 2026-08-12: 1,267 active tables with needs_compaction=True matched "
            "no enabled preset, including a 24TB table pinned to a preset that had been "
            "disabled -- found only via manual investigation, with no metric surfacing it."
        ),
    )

    # Slack notifications
    slack_webhook_url: str | None = Field(
        default=None,
        description="Slack incoming webhook URL for daily reports.",
    )
    slack_channel: str | None = Field(
        default=None,
        description="Override channel for Slack notifications (optional).",
    )
    slack_daily_report_cron: str = Field(
        default="0 9 * * *",
        description="Cron expression for daily anomaly report (default: 9 AM daily).",
    )
    slack_daily_report_enabled: bool = Field(
        default=False,
        description="Enable daily anomaly report to Slack.",
    )

    # Maintenance policy defaults
    compute_statistics_enabled: bool = Field(
        default=True,
        description="Enable Puffin statistics computation after compaction by default.",
    )
    stats_columns_limit: int = Field(
        default=50,
        description="Max columns to compute stats for (high-cardinality priority).",
    )
    benefit_score_update_interval_hours: int = Field(
        default=6,
        ge=1,
        description="Hours between AutoComp benefit score recalculations.",
    )
    benefit_score_sweep_via_k8s_jobs: bool = Field(
        default=False,
        description=(
            "Recovery plan §2/§8, 2026-09-20: when true, BenefitScoreUpdaterRunner's tick "
            "ensures a durable analysis_tasks row is due and dispatches a bounded K8s Job "
            "(cryo.analysis.dispatch) to process it, instead of running the multi-hour sweep "
            "in-process (the scheduler-OOM root cause this whole mechanism replaces). Defaults "
            "to False so upgrading this chart does not silently cut over production before the "
            "plan's required shadow-run/burn-in gate passes -- flip only after analysis.enabled "
            "is deployed and the runner has been observed clean across several real cycles."
        ),
    )
    evolving_catalog_cleanup_interval_hours: int = Field(
        default=6,
        ge=1,
        description=(
            "Hours between sweeps deactivating orphaned '*_evolving' catalog entries "
            "(cryo.db.repositories.sources.deactivate_orphaned_evolving_catalog_entries) -- "
            "the fix existed since 2026-08-26 (ace7f4b) but was never wired to a scheduled "
            "runner, so the same historical rename artifact (1,200+ tables, location IS NULL, "
            "a verified-good sibling with a real location) kept re-accumulating as the fleet's "
            "single worst repeat_failures pattern every day the cleanup wasn't run manually."
        ),
    )
    cost_model_refresh_interval_hours: int = Field(
        default=6,
        ge=1,
        description=(
            "Hours between cost-model retrains (cryo.ml.cost_integration.refresh_cost_model_"
            "cache). Live outage, 2026-09-10: this used to run inline, unconditionally, on "
            "every single job completion on every worker pod (cryo/worker/worker.py) -- "
            "confirmed live as the real root cause of a fleet-wide worker OOM cascade "
            "(kernel-verified via /proc/1/smaps_rollup, and independently reproduced by "
            "OOMKilling a cryo-api pod via one call to /api/v1/ai/cost-model/evaluate, same "
            "build_outcome_dataset code path -- QueryLog/TableStatsHistory/TableCostHistory/ "
            "QueryCostAttributionDaily queries over a 90-day window with no row limit on any "
            "of the secondary queries). Moved to a periodic CostModelRefreshRunner daemon, "
            "matching the same fix shape as evolving_catalog_cleanup_interval_hours above."
        ),
    )
    trino_hot_preset_repin_interval_hours: int = Field(
        default=24,
        ge=1,
        description=(
            "Hours between repins of the daily top-N-by-Trino-query-frequency maintenance "
            "preset (cryo.scheduler.trino_hot_preset.repin_trino_hot_tables) -- daily by "
            "default, matching its purpose: concentrate maintenance effort on whichever tables "
            "are actually driving Trino scan cost right now, re-evaluated once a day."
        ),
    )
    trino_hot_preset_name: str = Field(
        default="daily-trino-hot-tables",
        description=(
            "Name of the Preset (see scripts/seed_trino_hot_preset.py) that "
            "TrinoHotPresetRunner repins daily -- must match an existing, seeded Preset.name."
        ),
    )
    trino_hot_preset_table_limit: int = Field(
        default=500,
        ge=1,
        description="How many top-Trino-query-frequency tables to pin to the preset each repin.",
    )

    # API protection
    api_rate_limit_rps: float = Field(
        default=0.5,
        gt=0,
        description="Token refill rate for expensive API endpoints (requests/sec).",
    )
    api_rate_limit_burst: int = Field(
        default=10,
        gt=0,
        description="Burst capacity for expensive API endpoint rate limiting.",
    )

    # Alerting integrations
    pagerduty_routing_key: str | None = Field(
        default=None,
        description="PagerDuty Events API v2 routing key for high-severity anomalies.",
    )
    web_app_base_url: str | None = Field(
        default=None,
        description="Base URL of the Cryo web app, used to build deep links in breach alerts.",
    )
    table_alert_dispatch_interval_hours: int = Field(
        default=1,
        ge=1,
        description="Hours between SLA/data-quality breach checks for owned/subscribed tables.",
    )
    table_alert_cooldown_hours: int = Field(
        default=24,
        ge=1,
        description="Minimum hours between repeat breach alerts for the same table.",
    )
    commit_race_alert_lookback_hours: int = Field(
        default=24,
        ge=1,
        description=(
            "Window scanned for native compaction runs that lost the optimistic-concurrency "
            "commit race (audit detail tagged 'commit_race_lost'); see "
            "docs/native-engine-scoping-research.md §3.2."
        ),
    )
    commit_race_alert_min_occurrences: int = Field(
        default=2,
        ge=1,
        description=(
            "Number of commit-race losses within the lookback window before a table is flagged "
            "as 'compaction losing the commit race'. 1 = alert on the first loss."
        ),
    )
    s3_log_ingest_interval_hours: int = Field(
        default=24,
        ge=1,
        description=(
            "Hours between S3 request-cost ingestion runs (docs/s3-request-cost-tracking.md "
            "§10) -- reads and aggregates every enabled bucket's log objects natively "
            "(no Trino/Hive-connector dependency)."
        ),
    )
    s3_log_ingest_max_objects_per_day: int = Field(
        default=20_000,
        ge=1,
        description=(
            "Safety cap on how many S3 access-log objects are read per date-partition per "
            "ingestion run. A busy bucket can deliver many small per-flush log files; this "
            "bounds S3 API call volume per run rather than unbounded object-count growth. "
            "Days that hit the cap are logged as a lower-bound (not exact) count, not an error."
        ),
    )
    s3_log_ingest_concurrency: int = Field(
        default=16,
        ge=1,
        description=(
            "Max concurrent S3 GetObject calls when reading log objects for a single bucket's "
            "ingestion run -- I/O-bound work, so higher than a typical CPU-bound worker pool."
        ),
    )
    layout_recommendation_max_tables_per_tick: int = Field(
        default=20,
        ge=1,
        description=(
            "Cap on how many tables get a layout-recommendation (re)generated per telemetry "
            "tick. generate_layout_recommendation() skips tables refreshed within the last 24h "
            "on its own, so this only bounds the cost of the *first* pass over a large backlog "
            "-- keeps a single tick from doing thousands of analyses (each with its own bloom-"
            "filter/column-stats work) in the same process that already runs the memory-heavy "
            "benefit-score updater. Lowered from 200 to 20, 2026-08-14: "
            "generate_layout_recommendation() now calls analyze_table_layout(), which -- as of "
            "the same date -- self-fetches real per-file column bounds via "
            "cryo.optimizer.layout_analyzer.fetch_file_stats (previously a silent no-op, see "
            "that function's docstring). Confirmed live: reading one real table's manifests "
            "took ~5s; 200 tables/tick at that rate is 15+ minutes of real Iceberg catalog I/O "
            "in a single scheduler tick, which this cap now has to actually account for rather "
            "than being free."
        ),
    )
    property_autopilot_enabled: bool = Field(
        default=False,
        description=(
            "Off by default -- opt-in. When true, the scheduler's telemetry tick "
            "automatically applies a table's tier1/action=apply property recommendations "
            "(metadata-only changes like write.target-file-size-bytes, "
            "write.metadata.delete-after-commit.enabled -- never a data rewrite). "
            "compute_property_recommendations() and apply_table_properties() were both "
            "already fully built and running (the former on a schedule, the latter behind "
            "a manual-only API route) but nothing had ever connected them -- confirmed live, "
            "2026-08-10, by grepping the whole codebase for a consumer of the recommendation "
            "action field and finding none. See cryo/scanner/property_autopilot.py."
        ),
    )
    property_autopilot_max_tables_per_tick: int = Field(
        default=100,
        ge=1,
        description=(
            "Cap on how many tables get their safe property recommendations applied per "
            "telemetry tick when property_autopilot_enabled is true -- same rationale as "
            "layout_recommendation_max_tables_per_tick above: bounds the cost of a single "
            "tick rather than trying to sweep the whole fleet in one pass."
        ),
    )
    fast_path_check_enabled: bool = Field(
        default=True,
        description=(
            "Cheap new-table health sweep on the telemetry tick (RFC Features 2+3). "
            "Flags FileDegradationSignal small-file risk and missing "
            "write.target-file-size-bytes via compute_property_recommendations; never "
            "enqueues optimize. Default on because the work is metadata/stats already "
            "in Postgres, not catalog I/O. See cryo/scanner/fast_path_check.py."
        ),
    )
    fast_path_check_max_tables_per_tick: int = Field(
        default=50,
        ge=1,
        description=(
            "Cap on how many newly discovered tables the fast-path sweep inspects per "
            "telemetry tick when fast_path_check_enabled is true."
        ),
    )
    layout_autopilot_enabled: bool = Field(
        default=False,
        description=(
            "Off by default -- opt-in, and a second gate on top of the per-preset opt-in "
            "below. When true, the scheduler's telemetry tick automatically approves and "
            "executes a table's pending sort/z-order LayoutRecommendation -- but ONLY for "
            "tables whose effective preset has params.auto_apply_layout_recommendations = "
            "true. Unlike property_autopilot_enabled (tier1, metadata-only, safe fleet-wide "
            "by construction), applying a layout recommendation is a real data rewrite "
            "(tier2, link_layout) -- see cryo/scanner/property_autopilot.py's tier2 rationale. "
            "It stays human-supervised per-table by requiring the preset itself to opt in, "
            "not a single global switch, matching the 'test -> adopt & evolve' rollout used "
            "for every other autonomy change in this system. See "
            "cryo/scheduler/service.py::_run_layout_autopilot."
        ),
    )
    layout_autopilot_max_tables_per_tick: int = Field(
        default=20,
        ge=1,
        description=(
            "Cap on how many tables get their pending layout recommendation auto-executed "
            "per telemetry tick when layout_autopilot_enabled is true. Kept far lower than "
            "property_autopilot_max_tables_per_tick since each one is a real Spark rewrite "
            "job, not a metadata-only property flip."
        ),
    )
    layout_effectiveness_max_tables_per_tick: int = Field(
        default=20,
        ge=1,
        description=(
            "Cap on how many applied-but-unmeasured LayoutRecommendation rows get a fresh "
            "observed_prune_improvement reading per telemetry tick (PDAE-8288). Always runs "
            "(not gated behind an enabled flag like layout_autopilot_enabled) -- unlike "
            "actually rewriting a table, measuring an already-applied rewrite's real effect "
            "is read-only analysis, no data mutation, safe fleet-wide by construction. See "
            "cryo.optimizer.layout_analyzer.measure_layout_recommendation_effectiveness and "
            "MIN_HOURS_BEFORE_MEASURING_EFFECTIVENESS for the wait-period rationale."
        ),
    )
    max_concurrent_jobs_per_metastore: int = Field(
        default=150,
        ge=1,
        description=(
            "Cross-destination cap on RUNNING jobs whose table's source shares the same "
            "Hive metastore URI, checked at native-engine admission time. Real incident,  "
            "2026-08-12: a Hive metastore OOM'd (heap exhaustion, JvmPauseMonitor and "
            "pool-6 threads all throwing OutOfMemoryError) with 322 jobs started in the "
            "surrounding 15 minutes and ~70 concurrently failing with literal 'failed to "
            "connect to metastore' -- a direct, foreseeable consequence of raising the "
            "Governor's per-wave admission throughput (PDAE-8352's async fallback-dispatch "
            "fix) without checking whether the downstream metastore had headroom for the "
            "resulting concurrency. native_datafusion's own slot_cap (e.g. 80) has no "
            "relationship to the shared metastore's real connection capacity -- this is a "
            "second, independent ceiling, keyed by metastore URI rather than destination "
            "since multiple destinations/sources can share one metastore. Deliberately "
            "generous (150) rather than tightly tuned -- the goal is a circuit breaker "
            "against a repeat of this exact incident, not throughput-optimal metastore "
            "sizing, which is the metastore operator's call, not Cryo's."
        ),
    )
    s3_request_cost_retention_days: int = Field(
        default=35,
        ge=1,
        description=(
            "Days of S3RequestCostDaily rows kept before the retention job prunes them -- a "
            "few days past the 30-day TableCost rollup window."
        ),
    )
    fleet_admission_rate_window_minutes: int = Field(
        default=15,
        ge=1,
        description=(
            "Rolling window used by fleet_admission_rate_cap -- see that field's docstring "
            "for the incident this pair guards against."
        ),
    )
    cost_ceiling_enabled: bool = Field(
        default=False,
        description=(
            "Enables cryo.reporting.cost_ceiling's real-dollar circuit breaker -- checks "
            "the cryo namespace's actual daily spend (used+unused, via Grafana's Athena "
            "CUR datasource) and pauses fleet-wide admission (the same "
            "MaintenanceControlState global pause an operator's manual pause button "
            "writes to) if it exceeds cost_ceiling_daily_usd. Off by default: requires "
            "CRYO_GRAFANA_TOKEN to be set (see cryo-secrets) and the scheduler's "
            "periodic check to be wired in -- turning this on without that wiring is a "
            "silent no-op, not a broken cap, but there's no point defaulting it on before "
            "both exist. User-requested, 2026-08-12: a hard $100/day ceiling after real "
            "data showed the namespace averaging $210.82/day over the trailing 14 days."
        ),
    )
    cost_ceiling_daily_usd: float = Field(
        default=250.0,
        gt=0,
        description=(
            "Daily used+unused USD ceiling for cost_ceiling_enabled's circuit breaker. "
            "Raised 100.0 -> 250.0 2026-09-04: the original $100 (2026-08-12) was "
            "calibrated against a namespace that was mostly idle due to since-fixed "
            "governor/scheduler bugs -- real spend at healthy throughput (once those "
            "fixes shipped and the fleet actually started processing its real backlog) "
            "runs ~$150-195/day. $100 left essentially no headroom above normal healthy "
            "variance, so trivial day-to-day noise repeatedly tripped a fleet-wide pause. "
            "See cost_ceiling_tolerance_pct for the second layer of protection against "
            "over-tightening this again."
        ),
    )
    cost_ceiling_tolerance_pct: float = Field(
        default=0.15,
        ge=0,
        description=(
            "Fraction above cost_ceiling_daily_usd that's treated as normal variance, not "
            "a breach worth halting the fleet for -- e.g. 0.15 means spend up to 15% over "
            "the ceiling only logs a warning; only spend beyond that pauses admission. "
            "Added 2026-09-04 after a $1.06 overage on a $100 ceiling (1.06% over) "
            "triggered a full fleet-wide pause that sat for 4+ hours before a human "
            "noticed -- the ceiling had no graduated response, so a rounding-error "
            "overage got the identical response as a genuine 5x spend spike. See "
            "cryo.reporting.cost_ceiling.enforce_cost_ceiling's own docstring for the "
            "full incident."
        ),
    )
    cost_ceiling_cluster_name: str = Field(
        default="",
        description=(
            "EKS cluster name as tagged in AWS CUR "
            "(resource_tags_aws_eks_cluster_name). Required when cost_ceiling_enabled is true."
        ),
    )
    cost_ceiling_namespace: str = Field(
        default="cryo",
        description="Kubernetes namespace to check against cost_ceiling_daily_usd.",
    )
    cost_ceiling_check_interval_minutes: int = Field(
        default=30,
        ge=1,
        description=(
            "How often the scheduler re-checks real spend. CUR data lags 24-48h, so "
            "checking much more often than this doesn't reveal new information -- this "
            "mainly controls how quickly a pause gets lifted once yesterday's lagged data "
            "catches up to show spend back under the ceiling."
        ),
    )
    grafana_base_url: str = Field(
        default="",
        description=(
            "Base URL for cost_ceiling's Grafana API calls, no trailing slash. "
            "Required when cost_ceiling_enabled is true."
        ),
    )
    trino_cost_pricing_enabled: bool = Field(
        default=False,
        description=(
            "Enables deriving a real $/GiB Trino compute price from the trino-db namespace's "
            "actual Grafana Athena CUR cost "
            "(cryo.reporting.cost_ceiling.fetch_namespace_cost_range) divided by real bytes "
            "scanned that day (QueryCostAttributionDaily's rollup), and "
            "writing it onto QueryCostAttributionDaily.estimated_scan_cost_usd -- previously "
            "left NULL everywhere, since Trino-on-EKS has no published per-byte price the way "
            "S3 does. Off by default: same CRYO_GRAFANA_TOKEN dependency as "
            "cost_ceiling_enabled, and turning this on without that secret set is a silent "
            "no-op, not a broken feature."
        ),
    )
    trino_cost_pricing_namespace: str = Field(
        default="trino-db",
        description="Kubernetes namespace to price against (see trino_cost_pricing_enabled).",
    )
    trino_cost_pricing_lookback_days: int = Field(
        default=10,
        ge=1,
        description=(
            "How many trailing days to (re-)price on each tick. Wider than CUR's 24-48h lag "
            "on purpose, so a day whose cost data was still incomplete on a prior tick gets "
            "priced once it catches up, not stuck NULL forever."
        ),
    )
    fleet_admission_rate_cap: int = Field(
        default=500,
        ge=1,
        description=(
            "Blunt, mechanism-agnostic ceiling on total jobs (any status, any destination, "
            "fleet-wide) admitted within fleet_admission_rate_window_minutes. Real incident, "
            "2026-08-11 evening: 11,824 native jobs ran in one day, with 100+ admitted in "
            "single-minute windows and 31% of the day's jobs failing -- the metastore-"
            "concurrency cap (max_concurrent_jobs_per_metastore) and the concurrent-write-"
            "race fix (native_admit.py's snapshot-mutation defer) both close specific "
            "mechanisms that burst caused, but neither bounds the burst itself -- a "
            "different downstream bottleneck could still choke on the same admission "
            "volume. This is the backstop: if the fleet is admitting jobs this fast, "
            "something is already wrong (a retry storm, a misconfigured preset re-firing, "
            "an operator's bulk trigger) and it's worth pausing new admissions rather than "
            "richocheting straight through cluster capacity into whatever breaks next. "
            "Deliberately generous (500 per 15min = ~33/min average, well above the ~44/min "
            "peak throughput the async fallback-dispatch fix was built to reach) -- this "
            "is a circuit breaker against a repeat of the exact 2026-08-11 burst shape, not "
            "a throughput limiter for normal operation."
        ),
    )

    anomaly_detection_batch_size: int = Field(
        default=100,
        gt=0,
        description="Tables processed per batch in fleet-wide anomaly detection.",
    )
    anomaly_history_rows_per_table: int = Field(
        default=60,
        gt=5,
        description="Max history rows loaded per table during anomaly detection.",
    )
    anomaly_max_results: int = Field(
        default=500,
        gt=0,
        description="Maximum anomaly alerts retained from a fleet-wide scan.",
    )

    # Puffin-sidecar column sketches (FORMATS '26 / arXiv:2605.30308)

    puffin_local_dir: str = Field(
        default=".local/puffin",
        description="On-disk directory for Puffin blobs in local dev (CRYO_PUFFIN_LOCAL_DIR).",
    )

    # Silent-data-loss detector (arXiv:2604.20081)

    silent_loss_check_interval_min: int = Field(
        default=5,
        gt=0,
        description=(
            "Minutes between silent-data-loss scans per table "
            "(CRYO_SILENT_LOSS_CHECK_INTERVAL_MIN)."
        ),
    )
    silent_loss_local_root: str = Field(
        default=".local/tables",
        description="Local filesystem root for table data in dev (CRYO_SILENT_LOSS_LOCAL_ROOT).",
    )
    silent_loss_batch_size: int = Field(
        default=500,
        gt=0,
        description=(
            "Tables swept per scheduler tick by the silent-data-loss sweep, not the whole "
            "fleet at once -- sweeping ~13k tables unbounded in one session accumulated every "
            "Table/TableStats ORM object simultaneously and OOMKilled cryo-scheduler every "
            "~5min in production. The sweep rotates through the fleet across ticks instead "
            "(CRYO_SILENT_LOSS_BATCH_SIZE)."
        ),
    )

    # Variance-aware QPP (CLOUD 2026 / arXiv:2606.03464)

    qpp_enabled: bool = Field(
        default=False,
        description=(
            "When true, the Governor calls QPPModel.predict() before admitting a maintenance "
            "job and refuses jobs whose predicted p95 exceeds the per-destination target."
        ),
    )
    qpp_target_p95_ms: int = Field(
        default=10 * 60 * 1000,  # 10 minutes
        gt=0,
        description="Default per-destination QPP p95 target in milliseconds.",
    )
    qpp_min_observations: int = Field(
        default=20,
        gt=0,
        description="Minimum observations before QPPModel trusts the learned regression.",
    )

    observation_lakehouse_path: str = Field(
        default=".local/observations",
        description="On-disk path where the observation lakehouse stores long-term actuations.",
    )

    # LLM defaults / guardrails
    llm_default_model_anthropic: str = Field(
        default="claude-sonnet-4-20250514",
        description="Default Anthropic model when provider row omits model.",
    )
    llm_default_model_openai: str = Field(
        default="gpt-4o",
        description="Default OpenAI model when provider row omits model.",
    )
    llm_default_model_azure_openai: str = Field(
        default="gpt4o",
        description="Default Azure OpenAI deployment when provider row omits model.",
    )
    llm_openai_base_url: str | None = Field(
        default=None,
        description=(
            "Optional OpenAI-compatible API base override (vLLM/Ollama/local gateways). "
            "When set, OpenAI provider calls are routed through this endpoint."
        ),
    )
    llm_per_call_max_tokens_default: int = Field(
        default=1200,
        ge=64,
        description=(
            "Default max completion tokens allowed per LLM call when provider budget omits it."
        ),
    )
    llm_daily_token_cap_default: int = Field(
        default=300_000,
        ge=1_000,
        description="Default per-provider daily token cap when provider budget omits it.",
    )
    llm_daily_cost_cap_usd_default: float = Field(
        default=15.0,
        ge=0.0,
        description=(
            "Default per-provider daily spend cap in USD "
            "(best-effort; derived from provider config pricing)."
        ),
    )
    llm_over_budget_mode: Literal["reject", "abstain"] = Field(
        default="abstain",
        description=(
            "Behavior when an LLM provider exceeds budget: hard reject or abstain-with-fallback."
        ),
    )
    ai_feature_diagnosis_enabled: bool = Field(
        default=True,
        description="Enable run-failure diagnosis API/UI surfaces.",
    )
    ai_feature_filter_translation_enabled: bool = Field(
        default=True,
        description="Enable natural-language table-filter translation.",
    )
    ai_feature_plan_narration_enabled: bool = Field(
        default=True,
        description="Enable optional plan narration for SQL intent in PlanPreview.",
    )
    ai_feature_anomaly_brief_enabled: bool = Field(
        default=True,
        description="Enable anomaly root-cause brief surfaces.",
    )
    mcp_run_preset_enabled: bool = Field(
        default=False,
        description="Allow write-capable MCP run_preset tool. Disabled by default for safety.",
    )

    # Plan Verifier (arXiv:2510.09567)
    plan_verifier_enabled: bool = Field(
        default=True,
        description=(
            "When false, /plans/verify and the enqueue-time verifier pass-through return 503. "
            "Lets ops kill the feature without redeploying."
        ),
    )
    durable_intents_enabled: bool = Field(
        default=False,
        description=(
            "Bind new maintenance jobs to immutable plans, monotonic table fences, attempts, and "
            "effect receipts. Safety-alpha migration flag; legacy jobs remain executable."
        ),
    )
    durable_intent_ttl_hours: int = Field(
        default=24,
        ge=1,
        le=168,
        description=(
            "Maximum time a bound maintenance intent remains executable. Scheduled intents start "
            "their TTL at scheduled dispatch time and are also capped by the run deadline."
        ),
    )

    # Smart maintenance ROI gate / forecasting
    roi_gate_enabled: bool = Field(
        default=False,
        description="Enable ROI-based governor gate for maintenance admission decisions.",
    )
    roi_gate_shadow_mode: bool = Field(
        default=True,
        description="Log ROI decisions without enforcing skip/defer while tuning thresholds.",
    )
    roi_min_bcr: float = Field(
        default=1.0,
        ge=0.0,
        description="Minimum benefit-cost ratio required for non-streaming tables.",
    )
    roi_min_bcr_streaming: float = Field(
        default=0.5,
        ge=0.0,
        description="Minimum benefit-cost ratio required for streaming/hot tables.",
    )
    roi_shadow_min_samples: int = Field(
        default=200,
        ge=10,
        description="Minimum shadow decisions before suggesting ROI enforcement.",
    )
    governor_bandit_tuning_enabled: bool = Field(
        default=False,
        description=(
            "When true, a Thompson-sampling bandit may override the admission config an "
            "operator set on a destination (per_replica_cap, high_load_ratio/fraction, "
            "default_cooldown_hours, min_benefit_score) and learning signals are recorded on "
            "every admission and deferral. Off by default because both halves were costly: "
            "admission became non-deterministic -- the concurrency cap was resampled every "
            "wave, so the same fleet state could admit a different amount each time, which "
            "makes the governor's behaviour unreproducible exactly when it is being debugged "
            "-- and recording did a load-modify-save of one Postgres row per job, so a wave "
            "deferring 800 jobs meant ~1,600 round trips and 800 commits contending on that "
            "single row, plus a read-modify-write of a node-local JSON file per admission "
            "that multiple governor replicas cannot keep consistent. The knobs it tunes are "
            "ones the operator already sets explicitly per destination."
        ),
    )
    governor_shadow_sample_rate: int = Field(
        default=20,
        ge=1,
        description=(
            "Evaluate the ROI-gate and resource-predictor shadow paths for 1 in N candidates "
            "per wave, not every one. Live-verified 2026-09-02: both paths do 2-3 real DB "
            "queries per candidate purely to log a shadow recommendation neither currently "
            "enforces (roi_gate_shadow_mode's own log-then-fall-through branch, and the "
            "resource predictor has no enforcement path at all yet) -- at ~800 candidates in "
            "a large wave that's 3000+ synchronous round-trips per wave for data nobody acts "
            "on, found to be the dominant remaining cost after the two unbounded-reconcile-"
            "scan bugs fixed the same day were resolved. Sampling keeps shadow-mode data "
            "collection alive (roi_shadow_min_samples's 200-sample target is still reached "
            "well within a single large wave at this rate) without paying the full per-"
            "candidate cost on every wave. 1 disables sampling (evaluates every candidate,"
            " the pre-fix behavior)."
        ),
    )
    forecast_window_days: int = Field(
        default=14,
        ge=3,
        description="Historical window used by EWMA workload forecaster.",
    )
    forecast_default_horizon_hours: int = Field(
        default=24,
        ge=1,
        description="Default workload forecast horizon for smart-maintenance API endpoints.",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
