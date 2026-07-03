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

    offpeak_start_ist: int = Field(default=0, ge=0, le=23)
    offpeak_end_ist: int = Field(default=7, ge=0, le=23)
    default_per_replica_optimize_cap: int = Field(default=5, gt=0)
    wave_wait_sec: int = Field(default=300, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
