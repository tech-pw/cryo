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
        default=8,
        ge=1,
        description="Max threads for parallel manifest fetching within a table.",
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
    query_gets_per_file: float = Field(default=1.0, ge=0)

    offpeak_start_ist: int = Field(default=0, ge=0, le=23)
    offpeak_end_ist: int = Field(default=7, ge=0, le=23)
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
    telemetry_athena_workgroup: str | None = Field(
        default=None,
        description="Optional Athena workgroup filter for query-log ingestion.",
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

    # Plan Verifier (arXiv:2510.09567)
    plan_verifier_enabled: bool = Field(
        default=True,
        description=(
            "When false, /plans/verify and the enqueue-time verifier pass-through return 503. "
            "Lets ops kill the feature without redeploying."
        ),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
