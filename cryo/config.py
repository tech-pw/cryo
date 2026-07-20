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
        default=200_000,
        gt=0,
        description=(
            "Skip Tier-1 metadata cost accounting (all-snapshot path map) when the "
            "current-snapshot file count meets or exceeds this threshold; fall back "
            "to heuristic cost to avoid OOM on huge tables."
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
    health_port: int = Field(
        default=8081,
        ge=1024,
        le=65535,
        description="HTTP port for /healthz on worker/governor/scheduler processes.",
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
        default=200,
        ge=1,
        description=(
            "Cap on how many tables get a layout-recommendation (re)generated per telemetry "
            "tick. generate_layout_recommendation() skips tables refreshed within the last 24h "
            "on its own, so this only bounds the cost of the *first* pass over a large backlog "
            "-- keeps a single tick from doing thousands of analyses (each with its own bloom-"
            "filter/column-stats work) in the same process that already runs the memory-heavy "
            "benefit-score updater."
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
        description="Default max completion tokens allowed per LLM call when provider budget omits it.",
    )
    llm_daily_token_cap_default: int = Field(
        default=300_000,
        ge=1_000,
        description="Default per-provider daily token cap when provider budget omits it.",
    )
    llm_daily_cost_cap_usd_default: float = Field(
        default=15.0,
        ge=0.0,
        description="Default per-provider daily spend cap in USD (best-effort; derived from provider config pricing).",
    )
    llm_over_budget_mode: Literal["reject", "abstain"] = Field(
        default="abstain",
        description="Behavior when an LLM provider exceeds budget: hard reject or abstain-with-fallback.",
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
