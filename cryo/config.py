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
    native_engine_enabled: bool = Field(
        default=False,
        description="Enable experimental cryo-native engine (disabled by default).",
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

    anomaly_detection_batch_size: int = Field(
        default=100,
        gt=0,
        description="Tables processed per batch in fleet-wide anomaly detection.",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
