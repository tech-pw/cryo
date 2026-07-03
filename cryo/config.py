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
    cors_allow_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    scan_num_chunks: int = Field(default=4, gt=0)
    scan_small_file_mb: float = Field(default=89.6, gt=0)
    scan_needs_compaction_threshold: int = Field(default=128, gt=0)

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
    wave_wait_sec: int = Field(default=300, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
